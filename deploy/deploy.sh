#!/usr/bin/env bash
# Build and (re)deploy one branch on the host, then clean up stale branches.
#   deploy/deploy.sh <branch> [git-sha]
# Runs on the self-hosted runner `tuttitrip-deploy` (on the host itself).
# Env: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_TUNNEL_ID,
#      CLOUDFLARE_ZONE_ID (optional), TUTTITRIP_AUTH0__DOMAIN/AUDIENCE and
#      TUTTITRIP_CORS_ORIGINS/_ORIGIN_REGEX (optional; app defaults otherwise).
# Host files (never committed), under $TT_STATE_DIR (~/tuttitrip):
#      deploy.env  POSTGRES_PASSWORD (generated on first run)
#      app.env     optional extra app env for every branch (e.g. OPENAI_API_KEY)
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

if [ ! -f "$TT_STATE_DIR/deploy.env" ]; then
  umask 077
  printf 'POSTGRES_PASSWORD=%s\n' "$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)" >"$TT_STATE_DIR/deploy.env"
  tt_log "generated $TT_STATE_DIR/deploy.env"
fi
# shellcheck disable=SC1091
. "$TT_STATE_DIR/deploy.env"

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

envfile=$(mktemp)
trap 'rm -f "$envfile"' EXIT
chmod 600 "$envfile"
{
  printf 'TUTTITRIP_ENVIRONMENT=%s\n' "$env"
  printf 'TUTTITRIP_DATABASE__HOST=%s\nTUTTITRIP_DATABASE__PORT=5432\n' "$TT_POSTGRES"
  printf 'TUTTITRIP_DATABASE__USER=tuttitrip\nTUTTITRIP_DATABASE__PASSWORD=%s\n' "$POSTGRES_PASSWORD"
  printf 'TUTTITRIP_DATABASE__NAME=%s\n' "$database"
  for var in TUTTITRIP_AUTH0__DOMAIN TUTTITRIP_AUTH0__AUDIENCE TUTTITRIP_CORS_ORIGINS \
    TUTTITRIP_CORS_ORIGIN_REGEX TUTTITRIP_LLM__MODEL; do
    [ -n "${!var:-}" ] && printf '%s=%s\n' "$var" "${!var}"
  done
  [ -f "$TT_STATE_DIR/app.env" ] && grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$TT_STATE_DIR/app.env"
} >"$envfile" || true

tt_log "running migrations on $database"
docker run --rm --network "$TT_NETWORK" --env-file "$envfile" "$image" alembic upgrade head

docker rm -f "$container" >/dev/null 2>&1 || true
docker run -d --name "$container" --network "$TT_NETWORK" --restart unless-stopped \
  "${labels[@]}" --label tuttitrip.role=api --env-file "$envfile" "$image" >/dev/null
tt_log "started $container"

for i in $(seq 60); do
  if curl -fsS -H "Host: $host" "http://$TT_GATEWAY_BIND/health" >/dev/null 2>&1; then break; fi
  [ "$i" = 60 ] && { tt_log "health check failed"; docker logs --tail 50 "$container" >&2; exit 1; }
  sleep 2
done
tt_log "healthy behind the gateway"

# --- routing -------------------------------------------------------------------
cf_ingress_ensure "$host" "http://$TT_GATEWAY_BIND"
cf_dns_ensure "$host"

# Old images of this environment (keep the one just deployed).
docker image ls --filter "label=tuttitrip.env=$env" --format '{{.Repository}}:{{.Tag}}' \
  | grep -vx "$image" | xargs -r docker rmi >/dev/null 2>&1 || true

flock -u 9
# A failed cleanup never fails the deploy (the cleanup workflow reports it).
"$here/cleanup.sh" || tt_log "WARNING: cleanup did not complete; deploy itself succeeded"
