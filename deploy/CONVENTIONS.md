# Deployment conventions: tuttitrip-backend + tuttitrip-worker

Shared contract between this repository (FastAPI backend) and
`HackYeah-TuttiTripTeam/tuttitrip-worker` (DBOS workflows). Both deploy to the
same host (`dellpromaxgb10`) through self-hosted runners. If you change a rule,
change it here first.

## Environments and names

`<env>` is `main`, `develop`, or the branch slug: lowercase, every run of
non-`[a-z0-9]` characters becomes `-`, trimmed, at most 49 characters
(`feature/cos tam` -> `feature-cos-tam`). Reference implementation:
`deploy/lib.sh` (`tt_env`), tested in `tests/test_deploy_naming.py`.

| Thing | main | develop | branch `<env>` |
| --- | --- | --- | --- |
| API container | `tuttitrip-api` | `tuttitrip-api-develop` | `tuttitrip-api-<env>` |
| API image | `tuttitrip-api:main-<sha12>` | `tuttitrip-api:develop-<sha12>` | `tuttitrip-api:<env>-<sha12>` |
| API URL | `https://tuttitrip-api.gburek.app` | `https://tuttitrip-api-develop.gburek.app` | `https://tuttitrip-api-<env>.gburek.app` |
| Worker container | `tuttitrip-worker-main` | `tuttitrip-worker-develop` | `tuttitrip-worker-<env>` |
| Worker image | `tuttitrip-worker:main` | `tuttitrip-worker:develop` | `tuttitrip-worker:<env>` |
| Database | `tuttitrip_main` | `tuttitrip_develop` | `tuttitrip_br_<env with - as _>` |
| Env file | `~/tuttitrip/envs/main.env` | `~/tuttitrip/envs/develop.env` | `~/tuttitrip/envs/<env>.env` |

Docker labels on every container we run: `tuttitrip.managed=true`,
`tuttitrip.env=<env>`, `tuttitrip.role=api|worker|postgres|gateway`.
Worker containers: `--network tuttitrip --restart unless-stopped`, no
published ports, no tunnel ingress.

## Shared infrastructure (owned by the backend deploy)

- Docker network `tuttitrip`.
- PostgreSQL container `tuttitrip-postgres` (`pgvector/pgvector:0.8.7-pg18-trixie`,
  volume `tuttitrip-postgres-data`), reachable as `tuttitrip-postgres:5432` on
  the `tuttitrip` network only. User `tuttitrip`; the password lives in
  `~/tuttitrip/deploy.env` (generated on first deploy, mode 600).
- One database per env. The backend creates it and runs its Alembic
  migrations before starting the API. The `vector` extension is enabled by a
  backend migration.
- Schemas: backend Alembic owns `public` (including vector columns). DBOS
  owns `dbos`. If the worker needs its own tables, it uses a schema
  `worker` with its own migrations, and never alters `public`.
- `~/tuttitrip/deploy.lock`: take `flock` on it for any change on the host
  (container swap, DB create/drop, cleanup).

## Env file per environment

The backend deploy writes `~/tuttitrip/envs/<env>.env` (mode 600) on every
deploy. Both containers of the env use it with `--env-file`. Contents:

```dotenv
TUTTITRIP_ENVIRONMENT=<env>
TUTTITRIP_DATABASE__HOST=tuttitrip-postgres
TUTTITRIP_DATABASE__PORT=5432
TUTTITRIP_DATABASE__USER=tuttitrip
TUTTITRIP_DATABASE__PASSWORD=...
TUTTITRIP_DATABASE__NAME=<database>
TUTTITRIP_DBOS__APPLICATION_VERSION=<env>
# For the worker (standard DBOS names):
DBOS_SYSTEM_DATABASE_URL=postgresql://tuttitrip:...@tuttitrip-postgres:5432/<database>
DBOS__APPVERSION=<env>
# Plus every line of ~/tuttitrip/app.env (shared extra secrets, e.g. OPENAI_API_KEY)
```

Worker-only secrets go to `~/tuttitrip/worker.env` (optional, created by hand,
mode 600). The worker deploy passes it as a second `--env-file`. Secrets are
never committed. CI secrets live in GitHub Actions secrets of each repo.

If `envs/<env>.env` does not exist yet (the backend never deployed that env),
the worker deploy builds and tags its image `tuttitrip-worker:<env>` and skips
starting the container. The next backend deploy of that env starts it (see the
fallback below).

## DBOS

- System database = the env's application database (`DBOS_SYSTEM_DATABASE_URL`
  above). DBOS keeps its tables in schema `dbos`. Python DBOS connects with
  psycopg 3 regardless of the driver in the URL.
- `DBOSClient` never runs DBOS schema migrations. Only a running worker
  creates the `dbos` schema, which is why every env with an API also gets a worker.
- Application name: `tuttitrip-worker` (worker `DBOSConfig["name"]` and the
  backend client's `application_name`). The queue only dequeues workflows
  owned by the same application name.
- Application version: constant per env, `<env>`. The worker does **not** set
  `application_version` in `DBOSConfig`, so DBOS reads `DBOS__APPVERSION`
  from the env file. The backend enqueues with `app_version=<env>`.
  Workers only dequeue workflows tagged with their own version, so the shared
  constant means backend jobs never wait for a version that no worker runs.
  Trade-off: a redeployed worker resumes in-flight workflows with new code.
- Serialization: portable JSON (`WorkflowSerializationFormat.PORTABLE`) for
  inputs, outputs and events. The default is pickle, which would need the
  same Python classes on both sides. Workflows take one positional argument,
  a JSON object, and return a JSON object. Each side validates them with its
  own Pydantic models.

## Workflow contract

Canonical definitions live in the worker repo. The backend mirrors them in
`src/tuttitrip/shared/jobs/contracts.py`.

| Workflow name | Queue | Input (JSON) | Output (JSON) | Events |
| --- | --- | --- | --- | --- |
| `generate_trip_plan` | `planning` | `{"trip_id": "<uuid>", "request": "<text>"}` | `{"destination": str, "days": int, "highlights": [str]}` | `progress` |

Event `progress`: `{"stage": "<short-name>", "percent": 0..100}`, set with
`DBOS.set_event("progress", ..., serialization_type=PORTABLE)`.

The backend passes the caller's Auth0 `sub` as `authenticated_user` on enqueue
and only shows a job to the user who started it.

## Worker image fallback (backend deploy)

After the API of env X is healthy, if no container `tuttitrip-worker-X` is
running, the backend deploy starts one from the first image that exists:
`tuttitrip-worker:X`, then `tuttitrip-worker:develop`, then `tuttitrip-worker:main`.
If none exists, it logs a warning. Jobs then stay `ENQUEUED` until a worker
starts. The backend deploy never replaces a running worker; the worker deploy
does that.

## Cleanup

- Backend cleanup (every backend deploy and on branch deletion) removes, for
  envs whose branch no longer exists in the backend repo: `tuttitrip-api-<env>`,
  `tuttitrip-api:<env>-*` images, `tuttitrip-worker-<env>` containers,
  `envs/<env>.env`, the `tuttitrip_br_*` database and the tunnel ingress.
  The env's database is gone at that point, so its worker container goes too,
  even if the worker repo still has that branch.
- It never removes `tuttitrip-worker:*` images. The worker repo cleans its own
  images, but only for branches that no longer exist in the worker repo.
- `main` and `develop` resources are never removed.
