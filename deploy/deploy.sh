#!/usr/bin/env bash
# Build and (re)deploy one branch on the host, then clean up stale branches.
#   deploy/deploy.sh <branch> [git-sha]
# Runs on the self-hosted runner `tuttitrip-deploy` (on the host itself).
# Env: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_TUNNEL_ID,
#      CLOUDFLARE_ZONE_ID (optional), TUTTITRIP_AUTH0__DOMAIN/AUDIENCE and
#      TUTTITRIP_CORS_ORIGINS/_ORIGIN_REGEX (optional; app defaults otherwise).
# Host files (never committed), under $TT_STATE_DIR (~/tuttitrip):
#      deploy.env  POSTGRES_PASSWORD, WORKER_DB_PASSWORD, DEMO_RESET_SECRET (generated on first run)
#      app.env     optional extra app env for every branch (e.g. OPENAI_API_KEY)
#      admin.env   admin tools (deploy/admin/setup.sh, run after a main deploy)
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/.." && pwd)
# shellcheck source=deploy/lib.sh
. "$here/lib.sh"
# shellcheck source=deploy/cloudflare.sh
. "$here/cloudflare.sh"

branch=${1:?usage: deploy.sh <branch> [sha]}
sha=${2:-$(git -C "$repo" rev-parse HEAD)}
env=$(tt_env "$branch")
[ -n "$env" ] || { tt_log "branch '$branch' has an empty slug"; exit 1; }
if [ "$branch" != main ] && [ "$branch" != develop ] && tt_is_persistent "$env"; then
  tt_log "branch '$branch' collides with a persistent environment"; exit 1
fi
container=$(tt_container "$env")
host=$(tt_hostname "$env")
database=$(tt_database "$env")
image="tuttitrip-api:$env-${sha:0:12}"

mkdir -p "$TT_STATE_DIR"
chmod 700 "$TT_STATE_DIR"
exec 9>"$TT_STATE_DIR/deploy.lock"
flock 9  # one deploy/cleanup at a time on this host

gen_password() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32; }
umask 077
touch "$TT_STATE_DIR/deploy.env"
for var in POSTGRES_PASSWORD WORKER_DB_PASSWORD DEMO_RESET_SECRET; do
  if ! grep -q "^$var=" "$TT_STATE_DIR/deploy.env"; then
    printf '%s=%s\n' "$var" "$(gen_password)" >>"$TT_STATE_DIR/deploy.env"
    tt_log "generated $var in $TT_STATE_DIR/deploy.env"
  fi
done
# shellcheck disable=SC1091
. "$TT_STATE_DIR/deploy.env"
mkdir -p "$TT_STATE_DIR/envs"

tt_log "deploying branch '$branch' ($sha) as env '$env' -> https://$host"

# --- shared infrastructure (created once, namespaced) ------------------------
docker network inspect "$TT_NETWORK" >/dev/null 2>&1 \
  || docker network create --label tuttitrip.managed=true "$TT_NETWORK" >/dev/null

if ! docker container inspect "$TT_POSTGRES" >/dev/null 2>&1; then
  tt_log "starting $TT_POSTGRES"
  docker run -d --name "$TT_POSTGRES" --network "$TT_NETWORK" --restart unless-stopped \
    --label tuttitrip.managed=true --label tuttitrip.role=postgres \
    -e POSTGRES_USER=tuttitrip -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" -e POSTGRES_DB=postgres \
    -v tuttitrip-postgres-data:/var/lib/postgresql \
    --health-cmd "pg_isready -U tuttitrip -d postgres" --health-interval 5s \
    pgvector/pgvector:0.8.7-pg18-trixie >/dev/null
fi
docker start "$TT_POSTGRES" >/dev/null
for _ in $(seq 60); do
  docker exec "$TT_POSTGRES" pg_isready -U tuttitrip -d postgres >/dev/null 2>&1 && break
  sleep 1
done
psql_admin() { docker exec -i "$TT_POSTGRES" psql -v ON_ERROR_STOP=1 -U tuttitrip -d postgres -tAc "$1"; }
if [ -z "$(psql_admin "SELECT 1 FROM pg_database WHERE datname = '$database'")" ]; then
  psql_admin "CREATE DATABASE \"$database\"" >/dev/null
  tt_log "created database $database"
fi
# Worker role: cluster-wide, least privilege; grants per database below.
psql_admin "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tuttitrip_worker') THEN CREATE ROLE tuttitrip_worker; END IF; END \$\$" >/dev/null
psql_admin "ALTER ROLE tuttitrip_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '$WORKER_DB_PASSWORD'" >/dev/null

mkdir -p "$TT_STATE_DIR/gateway"
cp "$here/gateway/nginx.conf" "$TT_STATE_DIR/gateway/nginx.conf"
gw_hash=$(sha256sum "$TT_STATE_DIR/gateway/nginx.conf" | cut -c1-16)
if [ "$(docker inspect -f '{{index .Config.Labels "tuttitrip.config-hash"}}' "$TT_GATEWAY" 2>/dev/null || true)" != "$gw_hash" ]; then
  tt_log "(re)creating $TT_GATEWAY on $TT_GATEWAY_BIND"
  docker rm -f "$TT_GATEWAY" >/dev/null 2>&1 || true
  docker run -d --name "$TT_GATEWAY" --network "$TT_NETWORK" --restart unless-stopped \
    --label tuttitrip.managed=true --label tuttitrip.role=gateway --label tuttitrip.config-hash="$gw_hash" \
    -p "$TT_GATEWAY_BIND:80" \
    -v "$TT_STATE_DIR/gateway/nginx.conf:/etc/nginx/nginx.conf:ro" \
    nginx:1.29-alpine >/dev/null
fi

# --- build, migrate, swap ------------------------------------------------------
labels=(--label tuttitrip.managed=true --label tuttitrip.env="$env" --label tuttitrip.branch="$branch" --label tuttitrip.sha="$sha")
docker build -q -t "$image" "${labels[@]}" "$repo" >/dev/null
tt_log "built $image"

# Env files shared with the worker repo (deploy/CONVENTIONS.md), mode 600.
envfile="$TT_STATE_DIR/envs/$env.env"
workerenv="$TT_STATE_DIR/envs/$env.worker.env"
# Per-environment secret: a preview container running branch code never holds
# the secret of main (or any other env).
demo_reset_secret=$(printf '%s:%s' "$DEMO_RESET_SECRET" "$env" | sha256sum | cut -d' ' -f1)
db_url() { printf 'postgresql://%s:%s@%s:5432/%s' "$1" "$2" "$TT_POSTGRES" "$database"; }
{
  printf 'TUTTITRIP_ENVIRONMENT=%s\n' "$env"
  printf 'TUTTITRIP_DATABASE__HOST=%s\nTUTTITRIP_DATABASE__PORT=5432\n' "$TT_POSTGRES"
  printf 'TUTTITRIP_DATABASE__USER=tuttitrip\nTUTTITRIP_DATABASE__PASSWORD=%s\n' "$POSTGRES_PASSWORD"
  printf 'TUTTITRIP_DATABASE__NAME=%s\n' "$database"
  printf 'TUTTITRIP_DBOS__APPLICATION_VERSION=%s\n' "$env"
  printf 'TUTTITRIP_DEMO__RESET_SECRET=%s\n' "$demo_reset_secret"
  # MCP server: its URL is also the Auth0 API identifier of this environment.
  printf 'TUTTITRIP_MCP__ENABLED=true\nTUTTITRIP_MCP__RESOURCE_URL=https://%s/api/v1/mcp\n' "$host"
  for var in TUTTITRIP_AUTH0__DOMAIN TUTTITRIP_AUTH0__AUDIENCE TUTTITRIP_CORS_ORIGINS \
    TUTTITRIP_CORS_ORIGIN_REGEX; do
    if [ -n "${!var:-}" ]; then printf '%s=%s\n' "$var" "${!var}"; fi
  done
  # X-Forwarded-For is trusted only from the gateway's Docker network (uvicorn
  # reads FORWARDED_ALLOW_IPS); fall back to the private ranges if unknown.
  printf 'FORWARDED_ALLOW_IPS=%s\n' "$(docker network inspect "$TT_NETWORK" \
    -f '{{range .IPAM.Config}}{{.Subnet}},{{end}}' 2>/dev/null | sed 's/,$//;s/,\{2,\}/,/g' \
    | grep . || echo '172.16.0.0/12,192.168.0.0/16,10.0.0.0/8')"
  if [ -f "$TT_STATE_DIR/app.env" ]; then grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$TT_STATE_DIR/app.env" || true; fi
} >"$envfile"
{
  printf 'TUTTITRIP_ENVIRONMENT=%s\n' "$env"
  printf 'DBOS_SYSTEM_DATABASE_URL=%s\n' "$(db_url tuttitrip_worker "$WORKER_DB_PASSWORD")"
  printf 'TUTTITRIP_WORKER_DATABASE_URL=%s\n' "$(db_url tuttitrip_worker "$WORKER_DB_PASSWORD")"
  printf 'DBOS__APPVERSION=%s\n' "$env"
  printf 'TUTTITRIP_DEMO__RESET_SECRET=%s\n' "$demo_reset_secret"
  if [ -f "$TT_STATE_DIR/app.env" ]; then grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$TT_STATE_DIR/app.env" || true; fi
} >"$workerenv"

tt_log "running migrations on $database"
docker run --rm --network "$TT_NETWORK" --env-file "$envfile" "$image" alembic upgrade head
# DBOS system tables (schema "dbos") are created by the backend, as the owner,
# with access granted to the worker role, so the worker never runs DDL.
docker run --rm --network "$TT_NETWORK" -e DBOS_URL="$(db_url tuttitrip "$POSTGRES_PASSWORD")" "$image" \
  sh -c 'dbos migrate -s "$DBOS_URL" -r tuttitrip_worker' >/dev/null
docker exec -i "$TT_POSTGRES" psql -q -v ON_ERROR_STOP=1 -v dbname="$database" -U tuttitrip -d "$database" \
  <"$here/worker-grants.sql" >/dev/null
tt_log "migrations, DBOS schema and worker grants applied"

docker rm -f "$container" >/dev/null 2>&1 || true
docker run -d --name "$container" --network "$TT_NETWORK" --restart unless-stopped \
  "${labels[@]}" --label tuttitrip.role=api --env-file "$envfile" "$image" >/dev/null
tt_log "started $container"

for i in $(seq 60); do
  if curl -fsS -H "Host: $host" "http://$TT_GATEWAY_BIND/api/v1/health" >/dev/null 2>&1; then break; fi
  [ "$i" = 60 ] && { tt_log "health check failed"; docker logs --tail 50 "$container" >&2; exit 1; }
  sleep 2
done
tt_log "healthy behind the gateway"

# --- routing -------------------------------------------------------------------
cf_ingress_ensure "$host" "http://$TT_GATEWAY_BIND"
cf_dns_ensure "$host"

# Demo account data (jury login) once routing is live: reset to the sample set.
# Bounded, logged to a file, and never fails the deploy.
if grep -qE '^TUTTITRIP_DEMO__TOKEN_SHA256=.' "$envfile"; then
  demolog="$TT_STATE_DIR/demo-seed-$env.log"
  if timeout -k 5 120 docker exec "$container" python -m tuttitrip.demo.services.seed_command >"$demolog" 2>&1; then
    tt_log "demo account data reset"
  else
    tt_log "WARNING: demo account reset failed (see $demolog)"; tail -n 5 "$demolog" >&2 || true
  fi
fi

# Old images of this environment (keep the one just deployed).
docker image ls tuttitrip-api --filter "label=tuttitrip.env=$env" --format '{{.Repository}}:{{.Tag}}' \
  | grep -vx "$image" | xargs -r docker rmi >/dev/null 2>&1 || true

# --- worker (fallback image) and end-to-end smoke test -------------------------
worker="tuttitrip-worker-$env"
if [ "$(docker inspect -f '{{.State.Running}}' "$worker" 2>/dev/null || true)" != true ]; then
  worker_image=""
  for tag in "$env" develop main; do
    if docker image inspect "tuttitrip-worker:$tag" >/dev/null 2>&1; then worker_image="tuttitrip-worker:$tag"; break; fi
  done
  if [ -n "$worker_image" ]; then
    docker rm -f "$worker" >/dev/null 2>&1 || true
    extra=()
    if [ -f "$TT_STATE_DIR/worker.env" ]; then extra=(--env-file "$TT_STATE_DIR/worker.env"); fi
    # Same flags as the worker repo's own deploy (tt_start_worker there).
    docker run -d --name "$worker" --network "$TT_NETWORK" --restart unless-stopped \
      --stop-timeout 40 \
      --label tuttitrip.managed=true --label tuttitrip.env="$env" --label tuttitrip.role=worker \
      --env-file "$workerenv" "${extra[@]}" "$worker_image" >/dev/null
    # Local models (Ollama embeddings) live on the host's ollama_net network.
    for net in ${TT_WORKER_EXTRA_NETWORKS:-ollama_net}; do
      if docker network inspect "$net" >/dev/null 2>&1; then
        docker network connect "$net" "$worker" >/dev/null
      fi
    done
    tt_log "started $worker from fallback image $worker_image"
  else
    tt_log "WARNING: no tuttitrip-worker image (:$env, :develop, :main); jobs stay queued"
  fi
fi

if [ "$(docker inspect -f '{{.State.Running}}' "$worker" 2>/dev/null || true)" = true ]; then
  tt_log "smoke test: ping workflow through the worker"
  ping_id=$(curl -fsS -X POST -H "Host: $host" "http://$TT_GATEWAY_BIND/api/v1/jobs/ping" | jq -r .workflow_id)
  state=""
  for _ in $(seq 60); do
    state=$(curl -fsS -H "Host: $host" "http://$TT_GATEWAY_BIND/api/v1/jobs/ping/$ping_id" | jq -r .status)
    case "$state" in SUCCESS|ERROR|CANCELLED|MAX_RECOVERY_ATTEMPTS_EXCEEDED) break ;; esac
    sleep 3
  done
  if [ "$state" != SUCCESS ]; then
    tt_log "smoke test FAILED: ping $ping_id ended as '${state:-unknown}'"
    docker logs --tail 50 "$worker" >&2 || true
    exit 1
  fi
  tt_log "smoke test passed (ping $ping_id)"
else
  tt_log "WARNING: smoke test skipped, no worker running for $env"
fi

flock -u 9
# A failed cleanup never fails the deploy (the cleanup workflow reports it).
"$here/cleanup.sh" || tt_log "WARNING: cleanup did not complete; deploy itself succeeded"

# Admin tools (pgAdmin, DBOS dashboard behind Auth0) belong to production:
# only main refreshes them, and only on hosts that have ~/tuttitrip/admin.env.
if [ "$env" = main ]; then
  "$here/admin/setup.sh" || tt_log "WARNING: admin tools setup failed; API deploy itself succeeded"
fi
