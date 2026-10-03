---
name: new-permission
description: Register a feature node (READ/WRITE permission) in tuttitrip-backend, protect endpoints with requires(...), and give the default user role access through a migration. Use when adding an endpoint, a domain or subdomain, an admin-only capability, or when asked to "add a permission".
---

# New permission (feature node)

Rules are in AGENTS.md, section "Uprawnienia". Tests in
`tests/architecture/test_permissions.py` and `tests/shared/test_permissions.py`
fail when something is missing.

1. Pick the node in `src/tuttitrip/shared/permissions/registry.py`:
   - code = dotted path mirroring the domain: `<domain>` (group) and
     `<domain>.<sub>` (leaf). The parent must exist.
   - Endpoints need **leaves**. A group's own data gets `<domain>.core`.
   - Admin-only capability: put it under `admin.*`
     (e.g. `admin.planning_weights`), never next to user features.
2. Add the `Feature` member (`TRIPS_INVITATIONS = "trips.invitations"`) and its
   Polish description in `DESCRIPTIONS`.
3. Protect each route with exactly one marker:

   ```python
   from tuttitrip.shared.permissions.api import requires
   from tuttitrip.shared.permissions.registry import Access, Feature

   @router.post("", dependencies=[requires(Feature.TRIPS_INVITATIONS, Access.WRITE)])
   ```

   READ for reads and pure calculations, WRITE for changes. Use `Feature.X`,
   never the string code. Routes with `{trip_id}` also take
   `membership: TripMember` (or `TripCoHost`/`TripHost`) from
   `tuttitrip.trips.api` and pass it to the service.
4. Should normal users have it? Add a migration (skill `new-migration`, data
   only) that inserts into `role_grants` for role `user`:

   ```python
   op.execute(
       "INSERT INTO role_grants (role_name, feature, level) "
       "VALUES ('user', 'trips.invitations', 'WRITE') ON CONFLICT DO NOTHING"
   )
   ```

   Only leaves outside `admin.*`. `downgrade()` deletes the row. Superadmins
   (Auth0 `admin` claim) get every new node automatically; admin-only nodes
   need nothing else.
5. Frontend: the node shows up in `GET /api/v1/me` `access` and in
   `GET /admin/permissions/features`; OpenAPI shows `x-required-permission`.
6. Run `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`.
