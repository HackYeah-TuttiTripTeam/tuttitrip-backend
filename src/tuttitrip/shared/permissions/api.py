"""Authorization: ``requires``/``public`` markers, ``GET /me`` and the admin API.

Every route declares exactly one marker in ``dependencies=[...]``:

* ``requires(Feature.X, Access.READ|WRITE)``: 401 without a valid token,
  403 ``Missing permission x:LEVEL`` when the caller lacks it;
* ``public()``: no authentication (health, smoke test);
* ``token_access(TokenScope.X)``: no account, a scoped ``X-Access-Token`` bound
  to one profile of one trip; anything wrong with the token is a 404.

Permissions are resolved once per request (FastAPI caches dependencies within
a request) from one query, never across requests and never from the token,
except the Auth0 ``admin`` claim, which means ``*`` WRITE.
"""

from dataclasses import dataclass
from typing import Annotated, Any, override

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Response,
    params,
    status,
)
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.constants import (
    CACHE_CONTROL_HEADER,
    NO_STORE,
    NO_STORE_HEADERS,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.constants import AUDIT_DEFAULT_LIMIT, AUDIT_MAX_LIMIT
from tuttitrip.shared.permissions.logic.resolution import (
    EffectivePermissions,
    Grant,
    resolve,
)
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.shared.permissions.schemas import (
    AuditEntryRead,
    DirectGrantSet,
    FeatureNode,
    MeResponse,
    RoleCreate,
    RoleRead,
    RoleUpdate,
    TokenAccess,
    TokenScope,
    UserPermissionsRead,
)
from tuttitrip.shared.permissions.services import permission_service, token_service
from tuttitrip.shared.permissions.services.permission_service import (
    Actor,
    EscalationError,
    InvalidGrantError,
    ProtectedRoleError,
    RoleExistsError,
    RoleNotFoundError,
)

router = APIRouter()

OPENAPI_PERMISSION_KEY = "x-required-permission"
OPENAPI_PUBLIC_KEY = "x-public"
OPENAPI_TOKEN_KEY = "x-token-access"  # ruff: ignore[hardcoded-password-string] a header name, not a secret
ACCESS_TOKEN_HEADER = "X-Access-Token"  # ruff: ignore[hardcoded-password-string] a header name, not a secret


# --- resolution --------------------------------------------------------------


async def get_user_grants(user: CurrentUser, session: SessionDep) -> list[Grant]:
    """Load the caller's grants (one query; skipped for superadmins).

    Args:
        user: The authenticated caller.
        session: Database session.

    Returns:
        Grants from the caller's roles, the default role and direct grants.
    """
    if user.is_admin:
        return []  # the claim alone grants everything
    return await permission_service.load_grants(session, user.sub)


def get_effective_permissions(
    user: CurrentUser, grants: Annotated[list[Grant], Depends(get_user_grants)]
) -> EffectivePermissions:
    """Resolve the caller's permissions (cached for the rest of the request).

    Args:
        user: The authenticated caller.
        grants: The caller's stored grants.

    Returns:
        Effective permissions; the Auth0 ``admin`` claim adds ``*`` WRITE.
    """
    return resolve(grants, superadmin=user.is_admin)


EffectivePermissionsDep = Annotated[
    EffectivePermissions, Depends(get_effective_permissions)
]


# --- route markers -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PermissionRequirement:
    """Dependency that demands ``level`` on ``feature`` (see ``requires``)."""

    feature: Feature
    level: Access

    @override
    def __str__(self) -> str:
        return f"{self.feature.value}:{self.level.value}"

    async def __call__(self, permissions: EffectivePermissionsDep) -> None:
        """Raise 403 unless the caller holds the permission.

        Args:
            permissions: The caller's effective permissions.
        """
        if not permissions.allows(self.feature, self.level):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail=f"Missing permission {self}"
            )


@dataclass(frozen=True, slots=True)
class PublicMarker:
    """Dependency that marks a route as intentionally public (see ``public``)."""

    async def __call__(self) -> None:
        """Do nothing: the marker is only read by tests and OpenAPI."""


async def get_token_access(
    session: SessionDep,
    token: Annotated[
        str | None,
        Header(
            alias=ACCESS_TOKEN_HEADER,
            description="Secret access token (from the link fragment).",
        ),
    ] = None,
) -> TokenAccess:
    """Check the ``X-Access-Token`` header (once per request).

    Args:
        session: Database session.
        token: The presented token.

    Returns:
        Proof of access to one profile of one trip.

    Raises:
        HTTPException: 401 without the header, 404 for any bad token. The
            length is checked in the service (not by FastAPI), because a
            validation error would echo the secret back.
    """
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing access token",
            headers=NO_STORE_HEADERS,
        )
    try:
        return await token_service.authenticate(session, token)
    except token_service.InvalidTokenError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, TOKEN_NOT_FOUND, headers=NO_STORE_HEADERS
        ) from None


TokenAccessDep = Annotated[TokenAccess, Depends(get_token_access)]
TOKEN_NOT_FOUND = "Not found"  # ruff: ignore[hardcoded-password-string] a message, not a secret


@dataclass(frozen=True, slots=True)
class TokenRequirement:
    """Dependency that demands a valid token of ``scope`` (see ``token_access``)."""

    scope: TokenScope

    @override
    def __str__(self) -> str:
        return self.scope.value

    async def __call__(
        self, access: TokenAccessDep, session: SessionDep, response: Response
    ) -> None:
        """Raise 404 unless the token has the scope; then record the use.

        Args:
            access: The checked token.
            session: Database session.
            response: The response (gets ``Cache-Control: no-store``).
        """
        if access.scope != self.scope:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, TOKEN_NOT_FOUND, headers=NO_STORE_HEADERS
            )
        response.headers[CACHE_CONTROL_HEADER] = NO_STORE
        await token_service.touch(session, access.token_id)


def requires(feature: Feature, level: Access) -> params.Depends:
    """Route marker: the caller needs ``level`` on ``feature`` (or an ancestor).

    Usage::

        @router.get("", dependencies=[requires(Feature.TRIPS_CORE, Access.READ)])

    Args:
        feature: A registry feature (use a leaf).
        level: ``Access.READ`` or ``Access.WRITE`` (WRITE implies READ).

    Returns:
        The dependency to put in ``dependencies=[...]``.
    """
    return params.Depends(PermissionRequirement(feature, level))


def public() -> params.Depends:
    """Route marker: no authentication. Keep the list of public routes short.

    Returns:
        The dependency to put in ``dependencies=[...]``.
    """
    return params.Depends(PublicMarker())


def _set_no_store(response: Response) -> None:
    response.headers[CACHE_CONTROL_HEADER] = NO_STORE


def no_store() -> params.Depends:
    """Not a marker: ``Cache-Control: no-store`` for responses that carry secrets.

    Returns:
        The dependency to put in ``dependencies=[...]`` next to the marker.
    """
    return params.Depends(_set_no_store)


def token_access(scope: TokenScope) -> params.Depends:
    """Route marker: no account, a valid access token of ``scope``.

    The route takes ``TokenAccessDep`` to learn which trip and profile the
    token is bound to, and never has ``{trip_id}`` in its path (the trip comes
    from the token).

    Args:
        scope: What the token must allow.

    Returns:
        The dependency to put in ``dependencies=[...]``.
    """
    return params.Depends(TokenRequirement(scope))


Marker = PermissionRequirement | PublicMarker | TokenRequirement


def api_routes(app: FastAPI) -> list[RouteContext]:
    """Every API route as served, with router and include-level settings applied.

    FastAPI >= 0.142 keeps included routers nested; ``iter_route_contexts`` is
    what its own OpenAPI generator uses to flatten them.

    Args:
        app: The application.

    Returns:
        One context per route (``.path``, ``.methods``, ``.name``, ``.dependant``).
    """
    return [
        context
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
    ]


def route_markers(route: RouteContext) -> list[Marker]:
    """Every permission marker in a route's dependency tree.

    Includes router-level dependencies and markers nested in sub-dependencies.

    Args:
        route: A route from ``api_routes``.

    Returns:
        The markers found.
    """
    found: list[Marker] = []
    stack: list[Dependant] = list(route.dependant.dependencies)
    while stack:
        dependant = stack.pop()
        if isinstance(dependant.call, Marker):
            found.append(dependant.call)
        stack.extend(dependant.dependencies)
    return found


def _annotate(operation: dict[str, Any], marker: Marker) -> None:
    if isinstance(marker, PublicMarker):
        operation[OPENAPI_PUBLIC_KEY] = True
        line = "Publiczny: nie wymaga logowania."
    elif isinstance(marker, TokenRequirement):
        operation[OPENAPI_TOKEN_KEY] = str(marker)
        line = f"Dostęp tokenem bez konta (`{ACCESS_TOKEN_HEADER}`), zakres `{marker}`."
        responses = operation.setdefault("responses", {})
        responses.setdefault("401", {"description": "Brak nagłówka z tokenem"})
        responses.setdefault(
            "404", {"description": "Token nieznany, wygasły, odwołany albo inny zakres"}
        )
    else:
        operation[OPENAPI_PERMISSION_KEY] = str(marker)
        line = f"Wymagane uprawnienie: `{marker}`."
        responses = operation.setdefault("responses", {})
        responses.setdefault("401", {"description": "Brak tokenu albo zły token"})
        responses.setdefault("403", {"description": f"Brak uprawnienia `{marker}`"})
    operation["description"] = f"{operation.get('description', '')}\n\n{line}".strip()


def document_permissions(app: FastAPI) -> None:
    """Show each route's requirement in OpenAPI (extension + description line).

    Builds the schema once (FastAPI caches it in ``app.openapi_schema``) and
    annotates it: operations get ``x-required-permission: "feature:LEVEL"``,
    ``x-token-access: "scope"`` or ``x-public: true``, a Polish line in the
    description and 401/403 (token routes: 401/404) responses. Call it last
    in the app factory.

    Args:
        app: The application.
    """
    paths = app.openapi().get("paths", {})
    for route in api_routes(app):
        markers = route_markers(route)
        item = paths.get(route.path_format, {})
        for method in route.methods or ():
            operation = item.get(method.lower())
            if operation is not None and len(markers) == 1:
                _annotate(operation, markers[0])


# --- GET /me -----------------------------------------------------------------


@router.get(
    "/me",
    tags=["auth"],
    dependencies=[requires(Feature.ACCOUNTS_PROFILE, Access.READ)],
)
def read_me(user: CurrentUser, permissions: EffectivePermissionsDep) -> MeResponse:
    """Return the caller's identity and effective permissions.

    ``access`` maps every feature the caller can use to ``READ`` or ``WRITE``
    (groups already resolved), so the client can hide what is not allowed.

    Args:
        user: The authenticated caller.
        permissions: The caller's effective permissions.

    Returns:
        Identity, Auth0 roles and the flattened permission map.
    """
    return MeResponse(
        sub=user.sub,
        scopes=user.scopes,
        permissions=user.permissions,
        roles=user.roles,
        is_admin=user.is_admin,
        access=permissions.as_dict(),
    )


# --- access without an account ----------------------------------------------


@router.get("/vote/access", tags=["vote"], dependencies=[token_access(TokenScope.VOTE)])
def read_vote_access(access: TokenAccessDep) -> TokenAccess:
    """Say which trip and profile the voting token is bound to.

    Voting routes use the same marker and ``TokenAccessDep``; this one lets
    the voting page check its link before showing anything.

    Args:
        access: The checked token.

    Returns:
        Trip, profile and scope of the token.
    """
    return access


# --- admin API ---------------------------------------------------------------

_READ = [requires(Feature.ADMIN_PERMISSIONS, Access.READ)]
_WRITE = [requires(Feature.ADMIN_PERMISSIONS, Access.WRITE)]
_ADMIN = "/admin/permissions"


def get_actor(user: CurrentUser, permissions: EffectivePermissionsDep) -> Actor:
    """The admin behind a change, with their permissions (anti-escalation).

    Args:
        user: The authenticated caller.
        permissions: The caller's effective permissions.

    Returns:
        The actor.
    """
    return Actor(sub=user.sub, permissions=permissions)


ActorDep = Annotated[Actor, Depends(get_actor)]


_STATUS: dict[type[Exception], int] = {
    RoleNotFoundError: status.HTTP_404_NOT_FOUND,
    RoleExistsError: status.HTTP_409_CONFLICT,
    ProtectedRoleError: status.HTTP_403_FORBIDDEN,
    EscalationError: status.HTTP_403_FORBIDDEN,
    InvalidGrantError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}
_DOMAIN_ERRORS = tuple(_STATUS)


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(_STATUS[type(exc)], detail=str(exc))


@router.get(f"{_ADMIN}/features", tags=["admin"], dependencies=_READ)
def list_features() -> FeatureNode:
    """The feature tree with Polish descriptions (root ``*``).

    Returns:
        The tree.
    """
    return permission_service.feature_tree()


@router.get(f"{_ADMIN}/roles", tags=["admin"], dependencies=_READ)
async def list_roles(session: SessionDep) -> list[RoleRead]:
    """All roles with their grants.

    Args:
        session: Database session.

    Returns:
        Roles by name.
    """
    return await permission_service.list_roles(session)


@router.post(
    f"{_ADMIN}/roles",
    tags=["admin"],
    dependencies=_WRITE,
    status_code=status.HTTP_201_CREATED,
)
async def create_role(
    data: RoleCreate, actor: ActorDep, session: SessionDep
) -> RoleRead:
    """Create a role. Grants above your own level are refused (403).

    Args:
        data: Name, description and grants.
        actor: The admin.
        session: Database session.

    Returns:
        The created role.
    """
    try:
        return await permission_service.create_role(session, actor, data)
    except _DOMAIN_ERRORS as exc:
        raise _http_error(exc) from exc


@router.put(f"{_ADMIN}/roles/{{name}}", tags=["admin"], dependencies=_WRITE)
async def update_role(
    name: str, data: RoleUpdate, actor: ActorDep, session: SessionDep
) -> RoleRead:
    """Replace a role's description and grants (``superadmin`` is read-only).

    Args:
        name: Role name.
        data: Description and the full list of grants.
        actor: The admin.
        session: Database session.

    Returns:
        The updated role.
    """
    try:
        return await permission_service.update_role(session, actor, name, data)
    except _DOMAIN_ERRORS as exc:
        raise _http_error(exc) from exc


@router.delete(
    f"{_ADMIN}/roles/{{name}}",
    tags=["admin"],
    dependencies=_WRITE,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_role(name: str, actor: ActorDep, session: SessionDep) -> None:
    """Delete a role and its assignments (system roles cannot be deleted).

    Args:
        name: Role name.
        actor: The admin.
        session: Database session.
    """
    try:
        await permission_service.delete_role(session, actor, name)
    except _DOMAIN_ERRORS as exc:
        raise _http_error(exc) from exc


@router.get(f"{_ADMIN}/users", tags=["admin"], dependencies=_READ)
async def list_users(session: SessionDep) -> list[str]:
    """Users (Auth0 ``sub``) with an assigned role or a direct grant.

    Args:
        session: Database session.

    Returns:
        Subjects, sorted.
    """
    return await permission_service.list_users(session)


@router.get(f"{_ADMIN}/users/{{sub}}", tags=["admin"], dependencies=_READ)
async def read_user(sub: str, session: SessionDep) -> UserPermissionsRead:
    """A user's roles, direct grants and effective levels (without the claim).

    Args:
        sub: Auth0 subject (URL-encoded).
        session: Database session.

    Returns:
        The user's permissions.
    """
    return await permission_service.read_user(session, sub)


@router.put(
    f"{_ADMIN}/users/{{sub}}/roles/{{role}}", tags=["admin"], dependencies=_WRITE
)
async def assign_role(
    sub: str, role: str, actor: ActorDep, session: SessionDep
) -> UserPermissionsRead:
    """Assign a role (idempotent). ``superadmin`` only comes from Auth0 (403).

    Args:
        sub: Auth0 subject of the user.
        role: Role name.
        actor: The admin.
        session: Database session.

    Returns:
        The user's permissions afterwards.
    """
    try:
        return await permission_service.assign_role(session, actor, sub, role)
    except _DOMAIN_ERRORS as exc:
        raise _http_error(exc) from exc


@router.delete(
    f"{_ADMIN}/users/{{sub}}/roles/{{role}}", tags=["admin"], dependencies=_WRITE
)
async def revoke_role(
    sub: str, role: str, actor: ActorDep, session: SessionDep
) -> UserPermissionsRead:
    """Revoke a role (idempotent).

    Args:
        sub: Auth0 subject of the user.
        role: Role name.
        actor: The admin.
        session: Database session.

    Returns:
        The user's permissions afterwards.
    """
    return await permission_service.revoke_role(session, actor, sub, role)


@router.put(
    f"{_ADMIN}/users/{{sub}}/grants/{{feature}}", tags=["admin"], dependencies=_WRITE
)
async def set_grant(
    sub: str,
    feature: Feature,
    data: DirectGrantSet,
    actor: ActorDep,
    session: SessionDep,
) -> UserPermissionsRead:
    """Grant a level on a feature directly (not above your own level).

    Args:
        sub: Auth0 subject of the user.
        feature: Feature code (its subtree is included).
        data: The level.
        actor: The admin.
        session: Database session.

    Returns:
        The user's permissions afterwards.
    """
    try:
        return await permission_service.set_grant(
            session, actor, sub, feature, data.level
        )
    except _DOMAIN_ERRORS as exc:
        raise _http_error(exc) from exc


@router.delete(
    f"{_ADMIN}/users/{{sub}}/grants/{{feature}}", tags=["admin"], dependencies=_WRITE
)
async def revoke_grant(
    sub: str, feature: Feature, actor: ActorDep, session: SessionDep
) -> UserPermissionsRead:
    """Remove a direct grant (idempotent).

    Args:
        sub: Auth0 subject of the user.
        feature: Feature code.
        actor: The admin.
        session: Database session.

    Returns:
        The user's permissions afterwards.
    """
    return await permission_service.revoke_grant(session, actor, sub, feature)


@router.get(f"{_ADMIN}/audit", tags=["admin"], dependencies=_READ)
async def list_audit(
    session: SessionDep,
    sub: Annotated[str | None, Query(description="Only changes for this user.")] = None,
    limit: Annotated[int, Query(ge=1, le=AUDIT_MAX_LIMIT)] = AUDIT_DEFAULT_LIMIT,
) -> list[AuditEntryRead]:
    """Recent changes of roles and grants, newest first.

    Args:
        session: Database session.
        sub: Only entries about this user.
        limit: Maximum number of entries.

    Returns:
        Audit entries.
    """
    return await permission_service.list_audit(session, target_sub=sub, limit=limit)
