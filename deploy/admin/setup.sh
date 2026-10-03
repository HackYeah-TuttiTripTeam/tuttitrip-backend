#!/usr/bin/env bash
# Admin tools on the host, behind Auth0 and the superadmin allow-list:
#   https://tuttitrip-pgadmin.gburek.app  pgAdmin 4 (read-only servers)
#   https://tuttitrip-dbos.gburek.app     DBOS dashboard (tuttitrip-worker)
#
#   tunnel -> tuttitrip-admin-gateway (nginx on 172.17.0.1:18081, auth_request)
#          -> tuttitrip-oauth2-proxy (Auth0 OIDC + allow-list)
#          -> tuttitrip-pgadmin | tuttitrip-dbos-dashboard   (network tuttitrip-admin)
# The API's tuttitrip-gateway is not involved: every branch deploy rewrites it.
#
# Idempotent. Runs at the end of every deploy of main (deploy/deploy.sh) and
# can be run by hand on the host: deploy/admin/setup.sh
# Input (never committed): ~/tuttitrip/admin.env, mode 600, created by hand with
#   OAUTH2_PROXY_CLIENT_ID      Auth0 app "TuttiTrip Admin (oauth2-proxy)"
#   OAUTH2_PROXY_CLIENT_SECRET
#   SUPERADMIN_ALLOW_LIST       comma-separated emails and Discord user ids
# Generated into the same file on first run: READONLY_DB_PASSWORD,
#   PGADMIN_DEFAULT_PASSWORD, PGADMIN_MASTER_PASSWORD, PGADMIN_WEBSERVER_SECRET,
#   OAUTH2_PROXY_COOKIE_SECRET.
# Optional env for the tunnel routes (as in deploy.sh): CLOUDFLARE_API_TOKEN,
#   CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_TUNNEL_ID, CLOUDFLARE_ZONE_ID.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
deploy=$(cd "$here/.." && pwd)
# shellcheck source=deploy/lib.sh
. "$deploy/lib.sh"
# shellcheck source=deploy/cloudflare.sh
. "$deploy/cloudflare.sh"

admin_env="$TT_STATE_DIR/admin.env"
state="$TT_STATE_DIR/admin"
admin_net="tuttitrip-admin"
admin_gateway="tuttitrip-admin-gateway"
admin_bind="${TT_ADMIN_GATEWAY_BIND:-172.17.0.1:18081}"
auth0_issuer="https://${TUTTITRIP_AUTH0__DOMAIN:-dev-yahwm2zlut2gqdry.us.auth0.com}/"
hosts=("tuttitrip-pgadmin.$TT_DOMAIN" "tuttitrip-dbos.$TT_DOMAIN")
envs=(main develop)
log() { printf '[tuttitrip-admin] %s\n' "$*" >&2; }

if [ ! -f "$admin_env" ]; then
  log "no $admin_env: admin tools not configured on this host, skipping"
  exit 0
fi

exec 9>"$TT_STATE_DIR/deploy.lock"
flock 9
umask 077
mkdir -p "$state"
chmod 700 "$TT_STATE_DIR" "$state"
chmod 600 "$admin_env"

gen() { head -c 96 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "$1"; }
for spec in READONLY_DB_PASSWORD:32 PGADMIN_DEFAULT_PASSWORD:32 PGADMIN_MASTER_PASSWORD:48 \
  PGADMIN_WEBSERVER_SECRET:48 OAUTH2_PROXY_COOKIE_SECRET:32; do
  var=${spec%%:*}
  if ! grep -q "^$var=" "$admin_env"; then
    printf '%s=%s\n' "$var" "$(gen "${spec##*:}")" >>"$admin_env"
    log "generated $var in $admin_env"
  fi
done
set -a
# shellcheck disable=SC1090
. "$admin_env"
set +a
: "${OAUTH2_PROXY_CLIENT_ID:?missing in admin.env}" "${OAUTH2_PROXY_CLIENT_SECRET:?missing in admin.env}"
: "${SUPERADMIN_ALLOW_LIST:?missing in admin.env}"

# --- network, Postgres role ------------------------------------------------------
net_connect() {  # idempotent `docker network connect`
  docker inspect -f '{{json .NetworkSettings.Networks}}' "$2" | jq -e --arg n "$1" 'has($n)' >/dev/null \
    || docker network connect "$1" "$2" >/dev/null
}
docker network inspect "$admin_net" >/dev/null 2>&1 \
  || docker network create --label tuttitrip.managed=true --label tuttitrip.role=admin "$admin_net" >/dev/null
subnet=$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$admin_net")
net_connect "$admin_net" "$TT_POSTGRES"

docker exec -i "$TT_POSTGRES" psql -q -v ON_ERROR_STOP=1 -U tuttitrip -d postgres >/dev/null <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tuttitrip_readonly') THEN
    CREATE ROLE tuttitrip_readonly;
  END IF;
END \$\$;
ALTER ROLE tuttitrip_readonly WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  PASSWORD '$READONLY_DB_PASSWORD';
ALTER ROLE tuttitrip_readonly SET default_transaction_read_only = on;
SQL
for env in "${envs[@]}"; do
  db=$(tt_database "$env")
  if [ -n "$(docker exec "$TT_POSTGRES" psql -U tuttitrip -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$db'")" ]; then
    docker exec -i "$TT_POSTGRES" psql -q -v ON_ERROR_STOP=1 -v dbname="$db" -U tuttitrip -d "$db" \
      <"$here/readonly-grants.sql" >/dev/null
  fi
done
log "role tuttitrip_readonly ready (SELECT on public + dbos in ${envs[*]})"

# --- generated config (container-readable files are 644 inside the 700 dir) ----
install -m 644 "$here/compose.yaml" "$state/compose.yaml"
install -m 644 "$here/nginx.conf" "$state/nginx.conf"
mkdir -p "$state/nginx.d"
chmod 700 "$state/nginx.d"
printf 'tuttitrip-pgadmin.%s "%s";\n' "$TT_DOMAIN" "$PGADMIN_WEBSERVER_SECRET" >"$state/nginx.d/pgadmin-secret.map"
install -m 644 "$here/pgadmin/config_local.py" "$state/config_local.py"
install -m 755 "$here/pgadmin/master-password.sh" "$state/master-password.sh"

groups_toml=$(tr ',' '\n' <<<"$SUPERADMIN_ALLOW_LIST" | tr '[:upper:]' '[:lower:]' \
  | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -v '^$' | jq -R . | jq -sc .)
hosts_toml=$(printf '%s\n' "${hosts[@]}" | jq -R . | jq -sc .)
cat >"$state/oauth2-proxy.cfg" <<CFG
# Generated by deploy/admin/setup.sh from ~/tuttitrip/admin.env. Do not edit.
http_address = "0.0.0.0:4180"
reverse_proxy = true
provider = "oidc"
provider_display_name = "Auth0"
oidc_issuer_url = "$auth0_issuer"
client_id = "$OAUTH2_PROXY_CLIENT_ID"
client_secret = "$OAUTH2_PROXY_CLIENT_SECRET"
scope = "openid email profile"
code_challenge_method = "S256"
# Claims set by the Auth0 post-login Action for this client only.
oidc_email_claim = "https://tuttitrip.gburek.app/admin_email"
oidc_groups_claim = "https://tuttitrip.gburek.app/admin_ids"
insecure_oidc_allow_unverified_email = true
email_domains = ["*"]
# Second copy of the allow-list: the token must carry one of these identities.
allowed_groups = $groups_toml
upstreams = ["static://202"]
set_xauthrequest = true
skip_provider_button = true
whitelist_domains = $hosts_toml
# Only the gateway (on the tuttitrip-admin network) may set X-Forwarded-*.
trusted_proxy_ips = ["$subnet"]
cookie_name = "_tuttitrip_admin"
cookie_secret = "$OAUTH2_PROXY_COOKIE_SECRET"
cookie_secure = true
cookie_httponly = true
cookie_samesite = "lax"
cookie_expire = "24h"
cookie_refresh = "0s"
silence_ping_logging = true
CFG
chmod 644 "$state/oauth2-proxy.cfg"

{
  printf 'PGADMIN_DEFAULT_EMAIL=pgadmin@tuttitrip.gburek.app\n'
  printf 'PGADMIN_DEFAULT_PASSWORD=%s\n' "$PGADMIN_DEFAULT_PASSWORD"
  printf 'TUTTITRIP_PGADMIN_WEBSERVER_SECRET=%s\n' "$PGADMIN_WEBSERVER_SECRET"
  printf 'TUTTITRIP_PGADMIN_TRUSTED_PROXIES=%s\n' "$subnet"
} >"$state/pgadmin.env"
printf '%s' "$PGADMIN_MASTER_PASSWORD" >"$state/pgadmin_master_password"
chmod 644 "$state/pgadmin_master_password"

: >"$state/pg_service.conf"
servers='{}'
i=0
for env in "${envs[@]}"; do
  db=$(tt_database "$env")
  i=$((i + 1))
  printf '[%s_readonly]\nhost=%s\nport=5432\ndbname=%s\nuser=tuttitrip_readonly\npassword=%s\n\n' \
    "$db" "$TT_POSTGRES" "$db" "$READONLY_DB_PASSWORD" >>"$state/pg_service.conf"
  servers=$(jq --arg i "$i" --arg env "$env" --arg db "$db" --arg host "$TT_POSTGRES" '
    .[$i] = {Name: "TuttiTrip \($env) (read-only)", Group: "TuttiTrip", Host: $host, Port: 5432,
             MaintenanceDB: $db, Username: "tuttitrip_readonly", Service: "\($db)_readonly",
             DBRestriction: $db, Shared: true, SharedUsername: "tuttitrip_readonly",
             ConnectionParameters: {sslmode: "disable", connect_timeout: 10}}' <<<"$servers")
done
chmod 644 "$state/pg_service.conf"
jq '{Servers: .}' <<<"$servers" >"$state/servers.json"
chmod 644 "$state/servers.json"

{
  printf 'TUTTITRIP_DBOS_DASHBOARD_DATABASES='
  sep=''
  for env in "${envs[@]}"; do
    printf '%s%s=postgresql://tuttitrip_readonly:%s@%s:5432/%s' "$sep" "$env" "$READONLY_DB_PASSWORD" \
      "$TT_POSTGRES" "$(tt_database "$env")"
    sep=','
  done
  printf '\n'
} >"$state/dbos-dashboard.env"

# --- containers ------------------------------------------------------------------
export TT_ADMIN_STATE="$state" TT_ADMIN_GATEWAY_BIND="$admin_bind"
profiles=()
if docker run --rm --entrypoint sh tuttitrip-worker:main -c 'command -v tuttitrip-dbos-dashboard' >/dev/null 2>&1; then
  profiles=(--profile dbos)
else
  log "WARNING: tuttitrip-worker:main has no DBOS dashboard yet; it starts with the next worker deploy of main"
fi
applied=$(cat "$state"/{compose.yaml,nginx.conf,nginx.d/pgadmin-secret.map,config_local.py,master-password.sh,oauth2-proxy.cfg,pgadmin.env,pgadmin_master_password,pg_service.conf,servers.json,dbos-dashboard.env} | sha256sum | cut -c1-16)
recreate=()
if [ "$(cat "$state/.applied" 2>/dev/null || true)" != "$applied" ]; then recreate=(--force-recreate); fi
docker compose -f "$state/compose.yaml" --project-directory "$state" "${profiles[@]}" \
  up -d --quiet-pull --remove-orphans "${recreate[@]}" \
  || { docker compose -f "$state/compose.yaml" --project-directory "$state" "${profiles[@]}" ps >&2; exit 1; }
printf '%s\n' "$applied" >"$state/.applied"
log "containers up: $(docker ps --filter label=tuttitrip.role=admin --format '{{.Names}}' | sort | tr '\n' ' ')"

# --- health through the admin gateway ------------------------------------------------
for _ in $(seq 60); do
  docker exec "$admin_gateway" wget -qO- http://tuttitrip-pgadmin:80/misc/ping 2>/dev/null | grep -q PING && break
  sleep 2
done
docker exec "$admin_gateway" wget -qO- http://tuttitrip-pgadmin:80/misc/ping 2>/dev/null | grep -q PING \
  || { log "pgAdmin did not answer /misc/ping"; docker logs --tail 40 tuttitrip-pgadmin >&2; exit 1; }
for h in "${hosts[@]}"; do
  code=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -H "Host: $h" "http://$admin_bind/")
  case "$code" in
    "302 https://$h/oauth2/start"*) log "$h: anonymous request redirected to login (ok)" ;;
    *) log "$h: unexpected answer to an anonymous request: $code"; exit 1 ;;
  esac
done

# --- tunnel routes (same mechanism as the API deploy) ---------------------------------
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] && [ -n "${CLOUDFLARE_TUNNEL_ID:-}" ]; then
  for h in "${hosts[@]}"; do
    cf_ingress_ensure "$h" "http://$admin_bind"
    cf_dns_ensure "$h"
  done
else
  log "Cloudflare env not set: tunnel routes not checked"
fi
log "done"
