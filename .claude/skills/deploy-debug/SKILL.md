---
name: deploy-debug
description: Diagnose a failing or unhealthy tuttitrip-backend deployment on the dellpromaxgb10 host (GitHub Actions deploy job, containers, Postgres, nginx gateway, Cloudflare tunnel ingress). Use when a tuttitrip-api*.gburek.app URL is down or the deploy job failed.
---

# Debug a deployment

Read-only first. Only touch resources named `tuttitrip-*` / labelled
`tuttitrip.managed=true`; the host and the Cloudflare tunnel are shared.

1. CI side: `gh run list --limit 5`, then `gh run view <id> --log-failed`.
   The `deploy` job runs on the self-hosted runner `dellpromaxgb10-tuttitrip`
   (`gh api repos/HackYeah-TuttiTripTeam/tuttitrip-backend/actions/runners`).
2. Naming for a branch: `. deploy/lib.sh; e=$(tt_env "<branch>"); tt_container "$e"; tt_hostname "$e"; tt_database "$e"`.
3. On the host (`ssh cyprian@dellpromaxgb10`):
   - `docker ps -a --filter label=tuttitrip.managed=true`
   - `docker logs --tail 100 <container>`
   - readiness behind the gateway:
     `curl -s -H "Host: <hostname>" http://172.17.0.1:18080/api/v1/health`
     (`database: unavailable` -> check `tuttitrip-postgres`)
   - `docker exec tuttitrip-postgres psql -U tuttitrip -d postgres -c '\l'`
   - runner service: `systemctl status 'actions.runner.HackYeah-TuttiTripTeam-tuttitrip-backend.*'`
4. Public path: `curl -sI https://<hostname>/api/v1/health`.
   - 404 "page not found" (plain text): no ingress rule, so the request hit the
     shared wildcard. Check the rule order in the tunnel config (ours must be
     before `*.gburek.app`): source `deploy/lib.sh deploy/cloudflare.sh`, then `cf_ingress_list`.
   - 502: gateway cannot reach the container (name/network) or container is down.
   - Backups of every tunnel config change: `~/tuttitrip/backups/`.
5. Fix by re-running the workflow (`gh run rerun <id>`) or locally on the host:
   `deploy/deploy.sh <branch>` with the Cloudflare env vars exported. Stale
   branch resources: `deploy/cleanup.sh` (also triggered by branch deletion).
