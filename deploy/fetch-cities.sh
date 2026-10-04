#!/usr/bin/env bash
# Download the demo cities' Google Sheet (public, "anyone with the link") into
# the shared volume, replacing the last good copy only after the download passes
# every check. Never fails the deploy: any problem is a WARNING and the previous
# copy stays.
#   deploy/fetch-cities.sh
# Env:
#   TUTTITRIP_CITIES__SHEET_ID  sheet id; empty = skip with a warning
#   TT_CITIES_IMAGE             backend image: installs into the Docker volume
#                               (TT_CITIES_VOLUME) and validates the workbook
#                               with `import_command --check`
#   TT_CITIES_DIR               host directory instead of the volume (tests,
#                               local use); then no image is needed
#   TT_CITIES_URL               export URL override (tests); default Google's
#   TT_CITIES_PROTO             curl --proto for the first request (tests: =https,http)
#   TT_CITIES_MAX_TIME          seconds per attempt (default 60)
# Google's export URL is not a documented contract, so we check the file (ZIP
# signature, size, the five sheets), not just the HTTP status: a sheet that
# stopped being public answers 200 with a login page.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/lib.sh
. "$here/lib.sh"

file=miasta.xlsx
sheets=(miejsca noclegi komunikacja miasta slownik)
min_bytes=512
sheet_id=${TUTTITRIP_CITIES__SHEET_ID:-}
url=${TT_CITIES_URL:-"https://docs.google.com/spreadsheets/d/$sheet_id/export?format=xlsx"}
volume=${TT_CITIES_VOLUME:-tuttitrip-cities-data}

warn() { tt_log "WARNING: cities sheet: $* (keeping the last copy)"; }

if [ -z "$sheet_id" ]; then
  tt_log "WARNING: TUTTITRIP_CITIES__SHEET_ID is empty; skipping the cities sheet download (using the last copy)"
  exit 0
fi
case "$sheet_id" in *[!A-Za-z0-9_-]*) warn "TUTTITRIP_CITIES__SHEET_ID has unexpected characters"; exit 0 ;; esac
if [ -z "${TT_CITIES_DIR:-}" ] && [ -z "${TT_CITIES_IMAGE:-}" ]; then
  warn "neither TT_CITIES_DIR nor TT_CITIES_IMAGE is set"; exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
chmod 755 "$tmp"

# --fail: HTTP errors; --max-time and few retries: a slow Google must not hold
# the deploy lock for long.
if ! curl --fail --silent --show-error --location --proto "${TT_CITIES_PROTO:-=https}" --proto-redir =https --max-filesize 20000000 --max-time "${TT_CITIES_MAX_TIME:-60}" \
    --retry 2 --retry-delay 2 --user-agent "tuttitrip-deploy" \
    --output "$tmp/$file" "$url" 2>"$tmp/curl.err"; then
  warn "download failed ($(tr '\n' ' ' <"$tmp/curl.err" | cut -c1-200))"; exit 0
fi
size=$(stat -c %s "$tmp/$file")
if [ "$size" -lt "$min_bytes" ]; then warn "file is only $size bytes"; exit 0; fi
if [ "$(head -c2 "$tmp/$file")" != "PK" ]; then
  warn "response is not an XLSX file (a login page if the sheet stopped being public)"; exit 0
fi
# Sheet names are the contract; read them from the workbook, no extra tools.
if ! python3 - "$tmp/$file" "${sheets[@]}" <<'PY'
import re, sys, zipfile
path, *needed = sys.argv[1:]
try:
    xml = zipfile.ZipFile(path).read("xl/workbook.xml").decode()
except (zipfile.BadZipFile, KeyError, OSError) as exc:
    sys.exit(f"not a workbook: {exc}")
names = set(re.findall(r'<sheet\b[^>]*\bname="([^"]*)"', xml))
missing = [n for n in needed if n not in names]
sys.exit(f"missing sheets: {', '.join(missing)}" if missing else 0)
PY
then
  warn "the workbook does not have the required sheets"; exit 0
fi
chmod 644 "$tmp/$file"

if [ -n "${TT_CITIES_IMAGE:-}" ]; then
  # Full schema check with the code that will import it (no database needed).
  if ! out=$(docker run --rm --network none -v "$tmp:/in:ro" "$TT_CITIES_IMAGE" \
      python -m tuttitrip.places.services.import_command "/in/$file" --check 2>&1); then
    warn "the workbook does not match the import contract"
    printf '%s\n' "$out" | tail -n 15 >&2
    exit 0
  fi
  # The volume is root-owned when new; write as root, replace atomically.
  if ! docker run --rm --user 0 -v "$volume:/data/cities" -v "$tmp:/in:ro" "$TT_CITIES_IMAGE" \
      sh -c "cp /in/$file /data/cities/.$file.tmp && chmod 644 /data/cities/.$file.tmp \
        && mv -f /data/cities/.$file.tmp /data/cities/$file"; then
    warn "could not write the volume $volume"; exit 0
  fi
else
  mkdir -p "$TT_CITIES_DIR"
  cp "$tmp/$file" "$TT_CITIES_DIR/.$file.tmp" && mv -f "$TT_CITIES_DIR/.$file.tmp" "$TT_CITIES_DIR/$file" \
    || { warn "could not write $TT_CITIES_DIR"; exit 0; }
fi
tt_log "cities sheet downloaded ($size bytes) -> $file"
