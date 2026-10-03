#!/usr/bin/env bash
# Compare our contract mirror (contracts/jobs.schema.json) with the canonical
# file in tuttitrip-worker: same branch, else develop, else main.
# Skips with a warning when WORKER_REPO_TOKEN (GH_TOKEN) is not configured or
# the worker repo has no contract file yet; fails with a diff on mismatch.
set -euo pipefail
repo="HackYeah-TuttiTripTeam/tuttitrip-worker"
path="contracts/jobs.schema.json"
branch="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

if [ -z "${GH_TOKEN:-}" ]; then
  echo "::warning::WORKER_REPO_TOKEN secret is not set; contract drift check skipped."
  exit 0
fi

fetch() {
  curl -fsS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github.raw+json" \
    "https://api.github.com/repos/$repo/contents/$path?ref=$1"
}

theirs=$(mktemp)
trap 'rm -f "$theirs"' EXIT
used=""
for ref in "$branch" develop main; do
  if fetch "$ref" >"$theirs" 2>/dev/null; then used=$ref; break; fi
done
if [ -z "$used" ]; then
  echo "::warning::$repo has no $path on $branch, develop or main (or the token cannot read it); skipped."
  exit 0
fi

if diff -u --label "tuttitrip-worker@$used:$path" --label "tuttitrip-backend:$path" \
  <(jq -S . "$theirs") <(jq -S . "$path"); then
  echo "Contract matches tuttitrip-worker@$used."
else
  echo "::error::Job contract drift between tuttitrip-backend and tuttitrip-worker@$used (diff above)."
  echo "Update src/tuttitrip/shared/jobs/contracts.py to match the worker, regenerate $path, or follow the change procedure in AGENTS.md."
  exit 1
fi
