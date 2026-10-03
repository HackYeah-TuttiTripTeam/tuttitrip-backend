---
name: sync-contracts
description: Change or sync the job contract between tuttitrip-backend and tuttitrip-worker (workflow/queue names, payload models, events, CONTRACT_VERSION) and regenerate contracts/jobs.schema.json. Use when adding a background job, changing a job payload, or when the contracts-check CI job reports drift.
---

# Sync the job contract

The worker repo is canonical (`tuttitrip_worker/contracts.py` and
`contracts/jobs.schema.json`). This repo keeps a mirror in
`src/tuttitrip/shared/jobs/contracts.py`. Rules: `deploy/CONVENTIONS.md`,
section "Integracja z workerem".

## Drift reported by CI (`contracts-check`)

1. Read the diff in the job log (worker file vs ours).
2. Change the mirror models/enums so they match the worker exactly (field
   names, types, constraints, defaults; titles/descriptions are ignored).
3. Regenerate and test:
   ```bash
   uv run python -c "from tuttitrip.shared.jobs.contracts import contract_json; print(contract_json(), end='')" > contracts/jobs.schema.json
   uv run pytest tests/shared/test_jobs.py
   ```

## New workflow or compatible change (new optional field)

1. The worker PR lands first (it defines the workflow and exports the JSON).
2. Mirror it here: add the `Workflow`/`Queue` member, the input/output models
   (subclass `ContractPayload`, keep payloads small: ids + parameters), the
   `WORKFLOWS` entry and a `TIMEOUT_SECONDS` entry in
   `shared/jobs/services/job_queue.py`.
3. Enqueue it from a feature service (see `planning/services/plan_job_service.py`):
   `ensure_worker_available(session)`, then
   `queue.enqueue(Workflow.X, payload, user=sub, key=<domain id>)`, return `JobAccepted`.
4. Regenerate `contracts/jobs.schema.json`, add tests with `tests/shared/fakes.FakeJobQueue`.

## Incompatible change (needs a new CONTRACT_VERSION)

1. Worker: accept both versions (heartbeat `min_contract_version` = old,
   `contract_version` = new); deploy it.
2. Backend: bump `CONTRACT_VERSION`, change the models, regenerate the JSON.
   Merge when `contracts-check` is green against the worker.
3. Worker: drop the old version.

Never pass Pydantic objects to `DBOSClient` (pickle). The queue service sends
`payload.model_dump(mode="json")` with portable serialization.
