# Deployment conventions: tuttitrip-backend + tuttitrip-worker

Shared contract between this repository (FastAPI backend) and
`HackYeah-TuttiTripTeam/tuttitrip-worker` (DBOS workflows). Both deploy to the
same host (`dellpromaxgb10`) through self-hosted runners. Change a rule here
first, then in code.

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
| API env file | `~/tuttitrip/envs/main.env` | `~/tuttitrip/envs/develop.env` | `~/tuttitrip/envs/<env>.env` |
| Worker env file | `~/tuttitrip/envs/main.worker.env` | `~/tuttitrip/envs/develop.worker.env` | `~/tuttitrip/envs/<env>.worker.env` |

Docker labels on every container we run: `tuttitrip.managed=true`,
`tuttitrip.env=<env>`, `tuttitrip.role=api|worker|postgres|gateway`.
Worker containers: `--network tuttitrip --restart unless-stopped
--stop-timeout 40`, plus `docker network connect ollama_net` when that network
exists (local Ollama embeddings at `http://ollama:11434`); no published ports,
no tunnel ingress.

## Shared infrastructure (owned by the backend deploy)

- Docker network `tuttitrip`.
- PostgreSQL container `tuttitrip-postgres` (`pgvector/pgvector:0.8.7-pg18-trixie`,
  volume `tuttitrip-postgres-data`), reachable as `tuttitrip-postgres:5432` on
  the `tuttitrip` network only.
- One database per env, created by the backend deploy.
- `~/tuttitrip/deploy.env` (mode 600, generated): `POSTGRES_PASSWORD` (owner
  role `tuttitrip`) and `WORKER_DB_PASSWORD` (role `tuttitrip_worker`).
- `~/tuttitrip/deploy.lock`: take `flock` on it for any change on the host.

## Env files

The backend deploy rewrites both files on every deploy of `<env>` (mode 600):

`~/tuttitrip/envs/<env>.env` (API, owner role):

```dotenv
TUTTITRIP_ENVIRONMENT=<env>
TUTTITRIP_DATABASE__HOST=tuttitrip-postgres
TUTTITRIP_DATABASE__PORT=5432
TUTTITRIP_DATABASE__USER=tuttitrip
TUTTITRIP_DATABASE__PASSWORD=...
TUTTITRIP_DATABASE__NAME=<database>
TUTTITRIP_DBOS__APPLICATION_VERSION=<env>
# + Auth0/CORS settings and every line of ~/tuttitrip/app.env
```

`~/tuttitrip/envs/<env>.worker.env` (worker, restricted role):

```dotenv
TUTTITRIP_ENVIRONMENT=<env>
DBOS_SYSTEM_DATABASE_URL=postgresql://tuttitrip_worker:...@tuttitrip-postgres:5432/<database>
TUTTITRIP_WORKER_DATABASE_URL=postgresql://tuttitrip_worker:...@tuttitrip-postgres:5432/<database>
DBOS__APPVERSION=<env>
# + every line of ~/tuttitrip/app.env (shared secrets, e.g. OPENAI_API_KEY)
```

Worker-only secrets: `~/tuttitrip/worker.env` (optional, created by hand,
mode 600), passed as a second `--env-file`. Nothing here is ever committed.

If `envs/<env>.worker.env` does not exist yet (the backend never deployed that
env), the worker deploy builds and tags `tuttitrip-worker:<env>` and skips
starting the container. The next backend deploy starts it (fallback below).

## Integracja z workerem

### Who owns what

| Concern | Owner |
| --- | --- |
| Workflow code, queues, contract (canonical) | worker: `tuttitrip_worker/contracts.py`, `contracts/jobs.schema.json` |
| Contract mirror | backend: `src/tuttitrip/shared/jobs/contracts.py`, `contracts/jobs.schema.json` |
| All DDL: app tables (Alembic) and the DBOS schema (`dbos migrate`) | backend |
| Worker role `tuttitrip_worker` and its grants (`deploy/worker-grants.sql`) | backend deploy |
| Enqueue, status, cancel | backend, through `DBOSClient` only |
| Executing workflows, writing results, heartbeats | worker |

The backend never executes workflows. The worker never runs DDL and never
calls the backend over HTTP; it only talks to Postgres.

### Database access

- Role `tuttitrip_worker`: `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`.
  It has `SELECT` on `trips` and `profiles`, plus `SELECT, INSERT, UPDATE, DELETE`
  on `worker_heartbeats`, `job_results` and `embeddings`. Full access to the
  DBOS tables in schema `dbos` comes from `dbos migrate -r tuttitrip_worker`.
  The `tuttitrip` role owns everything.
- A table the worker needs is added by a backend migration plus a line in
  `deploy/worker-grants.sql` (a test checks the tables exist).
- DBOS system database = the env's application database, schema `dbos`.
  DBOS connects with psycopg 3 whatever driver the URL names.

### DBOS settings (worker)

```python
config: DBOSConfig = {
    "name": "tuttitrip-worker",  # = backend TUTTITRIP_DBOS__APPLICATION_NAME
    "system_database_url": os.environ["DBOS_SYSTEM_DATABASE_URL"],
    "run_migrations": False,  # the backend ran `dbos migrate`
    # no "application_version": DBOS reads DBOS__APPVERSION=<env>
}
DBOS(config=config)
DBOS.launch()
DBOS.register_queue("default")  # dbos>=3.2: after launch, no Queue(...)
DBOS.register_queue("local_llm")
DBOS.register_queue("openrouter")
```

Two DBOS behaviours drive this design (verified in dbos 3.2.0 source and
with a local round trip):

1. **Serialization.** `DBOSClient.enqueue` pickles arguments by default, and
   pickle needs the same Python classes in the worker. The backend therefore
   enqueues with `serialization_type=PORTABLE` and one JSON object argument.
   Workflows are declared with
   `@DBOS.workflow(name=..., serialization_type=WorkflowSerializationFormat.PORTABLE)`.
   Events use `serialization_type=PORTABLE` too.
2. **Application version.** A worker only dequeues workflows tagged with its
   own `application_version`. Version-less workflows go only to the
   latest registered version. Both sides use the constant `<env>`: the worker
   through `DBOS__APPVERSION`, the backend through `app_version` on enqueue.
   A redeployed worker resumes in-flight workflows with its new code (an
   accepted trade-off). The application name must match too.

### Contract and drift detection

- `CONTRACT_VERSION` (int) is a field of every input and output
  (`contract_version`, default = current version).
- Both repos render the contract with the same function and commit it as
  `contracts/jobs.schema.json`:

  ```python
  def _strip_docs(node):  # drop "title"/"description" recursively
      ...
  document = {
      "contract_version": CONTRACT_VERSION,
      "application_name": "tuttitrip-worker",
      "queues": sorted queue names,
      "workflows": {name: {"queue": q, "input": schema(In), "output": schema(Out)}},
      "events": {"progress": schema(Progress)},
  }
  json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
  ```

  `schema(M)` is `_strip_docs(M.model_json_schema())`. Reference:
  `contract_document()` in the backend mirror.
- Backend CI job `contracts-check` fetches the worker's file from the same
  branch, else `develop`, else `main`, and fails with a diff on mismatch.
  It needs the secret `WORKER_REPO_TOKEN` and skips with a warning until it exists.
- Change procedure for an incompatible change: (1) the worker accepts the
  old and the new version and its heartbeat advertises both; (2) the backend
  switches `CONTRACT_VERSION`; (3) the worker drops the old version.
  A worker that gets an unsupported version fails the workflow with a clear
  message. The backend shows it in `GET /jobs/{id}` (`status=ERROR`, `error`).

| Workflow | Queue | Input | Output | Timeout (backend) |
| --- | --- | --- | --- | --- |
| `generate_trip_plan` | `openrouter`, or `local_llm` when `provider="local"` | `{contract_version, trip_id, request, provider}` | `{contract_version, destination, days, highlights}` | 900 s |
| `embed_texts` | `default` | `{contract_version, source_kind, source_id, texts[1..64]}` | `{contract_version, model, dimensions, stored}` | 300 s |
| `ping` | `default` | `{contract_version, message}` | `{contract_version, message, worker_app_version}` | 60 s |

Event `progress`: `{"stage": str, "percent": 0..100}`. Small results are the
workflow output. Large or persistent results go to `job_results`
(`workflow_id`, `workflow_name`, `contract_version`, `result` JSONB) or
domain tables such as `embeddings`. The backend reads those.

### Idempotency, timeouts, cancellation

- Workflow id = `<workflow>-<domain id>-<sha256(canonical JSON payload)[:16]>`,
  enqueued with `workflow_id_reuse_policy="return-existing"`, so a repeated
  request returns the same job.
- The backend sets `workflow_timeout` per workflow (table above).
- `POST /jobs/{id}/cancel` cancels. Workflows should be written so that a
  cancellation between steps is safe.
- `authenticated_user` = the caller's Auth0 `sub`. Only that user sees the job.

### Worker liveness

- The worker runs a scheduled heartbeat workflow every ~30 s. It upserts
  `worker_heartbeats(worker_id PK, env, contract_version, min_contract_version,
  app_version, last_seen)` with `env = TUTTITRIP_ENVIRONMENT`, `contract_version` =
  the highest supported and `min_contract_version` = the lowest supported.
- Backend `/health` reports `worker: ok` (beat ≤ 90 s old), `stale`, or
  `missing` (none, or older than 600 s; both thresholds are settings). It also
  reports `contract_version` and `worker_contract_version`. If the backend
  version is outside `[min_contract_version, contract_version]`, `/health`
  answers 503 `degraded`.
- Enqueue endpoints answer 503 with a readable message while the worker is
  `missing` or incompatible.

### Smoke test after deploy

`POST /jobs/ping` (public) enqueues `ping`, and `GET /jobs/ping/{id}` shows it.
After every backend deploy, `deploy/deploy.sh` runs it through the gateway and
fails the deploy if the job does not reach `SUCCESS` within ~3 minutes. It
skips with a warning only when no worker container exists for the env. The
worker deploy runs the same check against `https://<api host>/jobs/ping`.

## Worker image fallback (backend deploy)

After the API of env X is healthy, if `tuttitrip-worker-X` is not running, the
backend deploy starts it from the first image that exists:
`tuttitrip-worker:X`, then `:develop`, then `:main`. It never replaces a
running worker; the worker deploy does that, reusing the same name, labels
and env files.

## Cleanup

- Backend cleanup (every backend deploy and on branch deletion) removes, for
  envs whose branch no longer exists in the backend repo: `tuttitrip-api-<env>`,
  `tuttitrip-api:<env>-*` images, `tuttitrip-worker-<env>`, both env files,
  the `tuttitrip_br_*` database and the tunnel ingress. The env's database is
  gone at that point, so its worker container goes too, even if the worker
  repo still has that branch.
- It never removes `tuttitrip-worker:*` images. The worker repo cleans its own
  images, but only for branches that no longer exist in the worker repo.
- `main` and `develop` resources are never removed.

## Local development (backend + worker)

1. Backend repo: `docker compose up -d --wait db`, `uv run alembic upgrade head`,
   `uv run dbos migrate -s postgresql://tuttitrip:tuttitrip@localhost:5432/tuttitrip`.
2. Worker repo: run the worker with
   `DBOS_SYSTEM_DATABASE_URL=postgresql://tuttitrip:tuttitrip@localhost:5432/tuttitrip`
   and `DBOS__APPVERSION=local` (= backend default `TUTTITRIP_DBOS__APPLICATION_VERSION`).
   Locally the worker may use the owner role. The restricted role is enforced
   on the host.
3. `curl -X POST localhost:8000/jobs/ping`, then `GET /jobs/ping/<id>`.
