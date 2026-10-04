#!/usr/bin/env bash
# Checks the part of the MCP login chain that needs no account:
#   deploy/smoke-mcp.sh https://tuttitrip-api-develop.gburek.app
# 1. POST /api/v1/mcp without a token answers 401 with a WWW-Authenticate header
#    that names the resource metadata (this is where every client starts);
# 2. the resource metadata (RFC 9728) names the same URL as the endpoint and an
#    authorization server;
# 3. that server's metadata has a registration_endpoint (DCR) and reports
#    whether it supports Client ID Metadata Documents (CIMD).
# Read-only: it creates nothing in Auth0. The login itself (Google, consent,
# token with the MCP audience) needs a real client: see README, "Serwer MCP".
set -euo pipefail
base=${1:?usage: smoke-mcp.sh <https://tuttitrip-api[-env].gburek.app>}
base=${base%/}
url="$base/api/v1/mcp"
ua='tuttitrip-smoke-mcp/1'  # Cloudflare blocks the default client agents
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

headers=$(curl -sS -o /dev/null -D - -A "$ua" -X POST "$url" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
printf '%s' "$headers" | head -n1 | grep -q ' 401' || fail "POST $url did not answer 401"
meta_url=$(printf '%s' "$headers" | tr -d '\r' \
  | sed -n 's/^[Ww][Ww][Ww]-[Aa]uthenticate:.*resource_metadata="\([^"]*\)".*/\1/p')
[ -n "$meta_url" ] || fail "no resource_metadata in WWW-Authenticate"
echo "ok  401 with resource_metadata=$meta_url"

meta=$(curl -sS -A "$ua" "$meta_url")
resource=$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["resource"])')
issuer=$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["authorization_servers"][0])')
[ "$resource" = "$url" ] || fail "resource '$resource' differs from the endpoint '$url' (it is the Auth0 API identifier)"
echo "ok  resource=$resource authorization_server=$issuer"

server=$(curl -sS -A "$ua" "${issuer%/}/.well-known/openid-configuration")
printf '%s' "$server" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("ok  registration_endpoint (DCR):", d.get("registration_endpoint", "MISSING"))
cimd = d.get("client_id_metadata_document_supported")
print("    CIMD:", "supported" if cimd else "not advertised (use \"Register automatically\")")
sys.exit(0 if d.get("registration_endpoint") else 1)
' || fail "the authorization server has no registration_endpoint (enable DCR)"
