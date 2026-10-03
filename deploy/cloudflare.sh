#!/usr/bin/env bash
# Cloudflare helpers: tunnel ingress (remotely managed tunnel) and DNS.
# Source after lib.sh. Needs: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID,
# CLOUDFLARE_TUNNEL_ID and (for DNS) CLOUDFLARE_ZONE_ID.
#
# The tunnel is shared with other services. We only ever add/remove rules
# whose hostname matches our pattern, insert them before any wildcard, and
# back up the full config before every write. Remotely managed tunnels pick
# up config changes live: cloudflared is never restarted.

CF_API="https://api.cloudflare.com/client/v4"
CF_MARK="managed-by=tuttitrip-deploy"
# Our hostnames: tuttitrip-api.<domain> and tuttitrip-api-<slug>.<domain>.
CF_HOST_RE="^${TT_PREFIX//./\\.}(-[a-z0-9-]+)?\\.${TT_DOMAIN//./\\.}$"
# Admin tools (deploy/admin/setup.sh). Never matched by CF_HOST_RE, so the
# branch cleanup never sees (or removes) them.
CF_ADMIN_HOST_RE="^tuttitrip-(pgadmin|dbos)\\.${TT_DOMAIN//./\\.}$"

cf_api() {
  local method=$1 url=$2 body=${3:-}
  local args=(-sS -X "$method" -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN:?}" \
    -H "Content-Type: application/json" "$CF_API$url")
  [ -n "$body" ] && args+=(--data "$body")
  curl "${args[@]}"
}

cf_config_url() { printf '/accounts/%s/cfd_tunnel/%s/configurations' "${CLOUDFLARE_ACCOUNT_ID:?}" "${CLOUDFLARE_TUNNEL_ID:?}"; }

# Print the tunnel config JSON (.result.config).
cf_get_config() {
  local resp
  resp=$(cf_api GET "$(cf_config_url)")
  if [ "$(jq -r '.success' <<<"$resp")" != true ]; then
    tt_log "cannot read tunnel config: $(jq -c '.errors' <<<"$resp")"; return 1
  fi
  jq '.result.config' <<<"$resp"
}

# cf_put_config <old-json> <new-json>: back up old, write new if different.
cf_put_config() {
  local old=$1 new=$2 backup resp
  if [ "$(jq -S . <<<"$old")" = "$(jq -S . <<<"$new")" ]; then tt_log "tunnel ingress unchanged"; return 0; fi
  mkdir -p "$TT_STATE_DIR/backups"
  backup="$TT_STATE_DIR/backups/tunnel-config-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
  printf '%s\n' "$old" >"$backup"
  # shellcheck disable=SC2012  # our own timestamped file names
  ls -1t "$TT_STATE_DIR"/backups/tunnel-config-*.json | tail -n +51 | xargs -r rm -f  # keep 50
  resp=$(cf_api PUT "$(cf_config_url)" "$(jq -c '{config: .}' <<<"$new")")
  if [ "$(jq -r '.success' <<<"$resp")" != true ]; then
    tt_log "tunnel config update failed: $(jq -c '.errors' <<<"$resp") (backup: $backup)"; return 1
  fi
  tt_log "tunnel ingress updated (previous config: $backup)"
}

# cf_ingress_ensure <hostname> <service>: our rule goes first, before wildcards.
cf_ingress_ensure() {
  local host=$1 service=$2 old new
  [[ $host =~ $CF_HOST_RE ]] || [[ $host =~ $CF_ADMIN_HOST_RE ]] \
    || { tt_log "refusing foreign hostname $host"; return 1; }
  old=$(cf_get_config) || return 1
  new=$(jq --arg h "$host" --arg s "$service" '
    .ingress = ([{hostname: $h, service: $s}] + [.ingress[] | select(.hostname != $h)])' <<<"$old")
  cf_put_config "$old" "$new"
}

# cf_ingress_remove <hostname>...: drop only these (ours) hostnames.
cf_ingress_remove() {
  local old new hosts
  for h in "$@"; do [[ $h =~ $CF_HOST_RE ]] || { tt_log "refusing foreign hostname $h"; return 1; }; done
  hosts=$(printf '%s\n' "$@" | jq -R . | jq -s .)
  old=$(cf_get_config) || return 1
  new=$(jq --argjson hs "$hosts" '.ingress = [.ingress[] | select((.hostname // "") as $h | ($hs | index($h)) | not)]' <<<"$old")
  cf_put_config "$old" "$new"
}

# Print our hostnames currently present in the tunnel ingress.
cf_ingress_list() {
  cf_get_config | jq -r --arg re "$CF_HOST_RE" '.ingress[].hostname // empty | select(test($re))'
}

# DNS is best-effort: a proxied wildcard *.<domain> may already cover us, and
# the token may lack Zone:DNS:Edit. Failures are logged, never fatal.
cf_dns_ensure() {
  local host=$1 resp
  [ -n "${CLOUDFLARE_ZONE_ID:-}" ] || { tt_log "CLOUDFLARE_ZONE_ID unset, skipping DNS"; return 0; }
  resp=$(cf_api GET "/zones/$CLOUDFLARE_ZONE_ID/dns_records?name=$host")
  if [ "$(jq -r '.success' <<<"$resp")" != true ]; then
    tt_log "DNS not managed (token lacks DNS access?): $(jq -c '.errors' <<<"$resp"); relying on wildcard"; return 0
  fi
  if [ "$(jq '.result | length' <<<"$resp")" != 0 ]; then tt_log "DNS record for $host exists"; return 0; fi
  resp=$(cf_api POST "/zones/$CLOUDFLARE_ZONE_ID/dns_records" "$(jq -nc --arg n "$host" \
    --arg c "$CLOUDFLARE_TUNNEL_ID.cfargotunnel.com" --arg m "$CF_MARK" \
    '{type: "CNAME", name: $n, content: $c, proxied: true, comment: $m}')")
  tt_log "DNS create $host: success=$(jq -r '.success' <<<"$resp") $(jq -c '.errors' <<<"$resp")"
}

cf_dns_remove() {
  local host=$1 resp
  [ -n "${CLOUDFLARE_ZONE_ID:-}" ] || return 0
  [[ $host =~ $CF_HOST_RE ]] || { tt_log "refusing foreign hostname $host"; return 1; }
  resp=$(cf_api GET "/zones/$CLOUDFLARE_ZONE_ID/dns_records?name=$host")
  [ "$(jq -r '.success' <<<"$resp")" = true ] || return 0
  # Only records we created (marked in the comment).
  jq -r --arg m "$CF_MARK" '.result[] | select(.comment == $m) | .id' <<<"$resp" | while read -r id; do
    cf_api DELETE "/zones/$CLOUDFLARE_ZONE_ID/dns_records/$id" >/dev/null && tt_log "DNS record $host removed"
  done
}
