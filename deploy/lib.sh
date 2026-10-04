#!/usr/bin/env bash
# Shared naming rules for deploy.sh and cleanup.sh. Source it, don't run it.
#
#   branch            env label   hostname                                  container              database
#   main              main        tuttitrip-api.gburek.app                  tuttitrip-api          tuttitrip_main
#   develop           develop     tuttitrip-api-develop.gburek.app          tuttitrip-api-develop  tuttitrip_develop
#   feature/cos tam   feature-cos-tam  tuttitrip-api-feature-cos-tam.gburek.app  tuttitrip-api-feature-cos-tam  tuttitrip_br_feature_cos_tam
#
# Everything this tooling creates is namespaced by the `tuttitrip-` prefix
# and/or the `tuttitrip.managed=true` Docker label; cleanup touches nothing else.

# shellcheck disable=SC2034  # variables are used by the scripts sourcing this file
TT_PREFIX="tuttitrip-api"
TT_DOMAIN="${TT_DOMAIN:-gburek.app}"
TT_NETWORK="tuttitrip"
TT_POSTGRES="tuttitrip-postgres"
# The cities sheet (one for every environment); cleanup.sh never removes it.
TT_CITIES_VOLUME="${TT_CITIES_VOLUME:-tuttitrip-cities-data}"
TT_GATEWAY="tuttitrip-gateway"
TT_STATE_DIR="${TT_STATE_DIR:-$HOME/tuttitrip}"
# The gateway listens on the docker0 bridge address only: reachable by the
# cloudflared container (on the default bridge), not from the LAN.
TT_GATEWAY_BIND="${TT_GATEWAY_BIND:-172.17.0.1:18080}"
# A DNS label is at most 63 chars; "tuttitrip-api-" takes 14 of them.
TT_MAX_SLUG=$((63 - ${#TT_PREFIX} - 1))

# Lowercase, every run of non [a-z0-9] chars -> "-", trimmed, max 49 chars.
tt_slugify() {
  local s
  s=$(printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]' \
      | LC_ALL=C sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')
  s=${s:0:$TT_MAX_SLUG}
  printf '%s' "${s%-}"  # truncation can leave one trailing dash
}

# Environment label for a branch: main, develop or the branch slug.
tt_env() {
  case "$1" in
    main) printf 'main' ;;
    develop) printf 'develop' ;;
    *) tt_slugify "$1" ;;
  esac
}

tt_is_persistent() { [ "$1" = main ] || [ "$1" = develop ]; }

tt_container() { if [ "$1" = main ]; then printf '%s' "$TT_PREFIX"; else printf '%s-%s' "$TT_PREFIX" "$1"; fi; }

tt_hostname() { printf '%s.%s' "$(tt_container "$1")" "$TT_DOMAIN"; }

tt_database() {
  if tt_is_persistent "$1"; then printf 'tuttitrip_%s' "$1"
  else printf 'tuttitrip_br_%s' "${1//-/_}"; fi
}

tt_log() { printf '[tuttitrip-deploy] %s\n' "$*" >&2; }

# MCP server settings of an environment (lines for the app env file). The server
# is on only where the Auth0 tenant has an API for it: main and develop. A
# preview branch would need an API per branch (the identifier is the URL), so
# there /api/v1/mcp answers 404.
tt_mcp_env() {
  if tt_is_persistent "$1"; then
    printf 'TUTTITRIP_MCP__ENABLED=true\nTUTTITRIP_MCP__RESOURCE_URL=https://%s/api/v1/mcp\n' "$(tt_hostname "$1")"
  else
    printf 'TUTTITRIP_MCP__ENABLED=false\n'
  fi
}
