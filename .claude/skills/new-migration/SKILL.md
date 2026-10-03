---
name: new-migration
description: Create, review and apply an Alembic migration in tuttitrip-backend after changing SQLAlchemy models (models.py). Use when tables or columns change, or when asked to "add a migration".
---

# New migration

1. Start Postgres if needed: `docker compose up -d --wait db`
   (if port 5432 is busy, set `TUTTITRIP_DATABASE__PORT=5433` in `.env`; compose
   uses the same variable for the host port).
2. Bring the DB to the current head: `uv run alembic upgrade head`.
3. Generate: `uv run alembic revision --autogenerate -m "<what changed>"`.
   Models are discovered automatically (every `tuttitrip.**.models` module);
   the post-write hooks run ruff on the new file.
4. Review `migrations/versions/<date>_<rev>_<slug>.py`:
   - autogenerate misses renames (it emits drop + add) and server-side things
     like extensions, enums or data migrations; write those by hand with `op.execute`;
   - `downgrade()` must really revert `upgrade()`.
5. Verify: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run alembic check`
   (`check` must say "No new upgrade operations detected").
6. If tuttitrip-worker must read or write the new table, add it to
   `deploy/worker-grants.sql` (read-only or read-write section);
   `tests/test_worker_grants.py` checks the names.
7. Run the full check suite and commit the migration with the model change.

Deployments run `alembic upgrade head` in a one-off container before the new
API container starts; never migrate from application code.
