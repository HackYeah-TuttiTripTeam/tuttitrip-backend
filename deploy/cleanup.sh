#!/usr/bin/env bash
# Remove deployments of branches that no longer exist on origin:
# container, images, feature database, tunnel ingress and DNS record.
# main and develop are never touched; neither is anything without our
# prefix/labels. Aborts if the branch list cannot be fetched.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/.." && pwd)
# shellcheck source=deploy/lib.sh
. "$here/lib.sh"
# shellcheck source=deploy/cloudflare.sh
. "$here/cloudflare.sh"

mkdir -p "$TT_STATE_DIR"
exec 9>"$TT_STATE_DIR/deploy.lock"
flock 9

branches=$(git -C "$repo" ls-remote --heads origin | sed 's#.*refs/heads/##')
if ! grep -qx main <<<"$branches" || ! grep -qx develop <<<"$branches"; then
  tt_log "branch list looks wrong (no main/develop), refusing to clean up"; exit 1
fi
declare -A live=()
while read -r b; do [ -n "$b" ] && live[$(tt_env "$b")]=1; done <<<"$branches"

# Candidate envs from every resource type we create.
mapfile -t envs < <(
  {
    docker ps -a --filter label=tuttitrip.managed=true --filter label=tuttitrip.role=api \
      --format '{{.Label "tuttitrip.env"}}'
    docker exec "$TT_POSTGRES" psql -U tuttitrip -d postgres -tAc \
      "SELECT substr(datname, 14) FROM pg_database WHERE datname LIKE 'tuttitrip\_br\_%'" 2>/dev/null \
      | tr '_' '-'
    cf_ingress_list 2>/dev/null | sed -E "s/^${TT_PREFIX}-?//; s/\\.${TT_DOMAIN//./\\.}$//"
  } | grep -v '^$' | sort -u
)

stale_hosts=()
for env in "${envs[@]}"; do
  [ -n "$env" ] || continue
  tt_is_persistent "$env" && continue
  [ -n "${live[$env]:-}" ] && continue
  tt_log "removing stale environment '$env'"
  container=$(tt_container "$env")
  if [ "$(docker inspect -f '{{index .Config.Labels "tuttitrip.managed"}}' "$container" 2>/dev/null || true)" = true ]; then
    docker rm -f "$container" >/dev/null
  fi
  docker image ls --filter label=tuttitrip.managed=true --filter "label=tuttitrip.env=$env" \
    --format '{{.Repository}}:{{.Tag}}' | xargs -r docker rmi >/dev/null 2>&1 || true
  db=$(tt_database "$env")
  if [[ $db =~ ^tuttitrip_br_[a-z0-9_]+$ ]]; then
    docker exec "$TT_POSTGRES" psql -U tuttitrip -d postgres -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)" >/dev/null 2>&1 || true
  fi
  stale_hosts+=("$(tt_hostname "$env")")
done

if [ "${#stale_hosts[@]}" -gt 0 ]; then
  cf_ingress_remove "${stale_hosts[@]}"
  for h in "${stale_hosts[@]}"; do cf_dns_remove "$h"; done
fi
tt_log "cleanup done (${#stale_hosts[@]} stale environment(s))"
