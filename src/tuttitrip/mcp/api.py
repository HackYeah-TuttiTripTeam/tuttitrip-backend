"""The MCP server (Streamable HTTP) at ``/api/v1/mcp``.

Authentication is an Auth0 access token whose audience is the MCP endpoint URL
(``Settings.mcp.resource_url``), not the audience of the app API, so a token
for one never works for the other. Authorization has two layers, as in the REST
API: feature permissions (``mcp_requires``, one per tool, resolved once per
request from the database) and, inside the tool, trip roles through the trips
service.

``create_mcp`` returns a fresh server, ``create_mcp_app`` the ASGI app and the
well-known routes that ``tuttitrip.main`` mounts. Never run it over stdio:
FastMCP skips ``auth`` checks there.
"""

import asyncio
from dataclasses import dataclass
from typing import Annotated, override
from urllib.parse import urlsplit
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import (
    AccessToken,
    AuthContext,
    RemoteAuthProvider,
    TokenVerifier,
)
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.http import StarletteWithLifespan
from pydantic import AnyHttpUrl, Field
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from tuttitrip.mcp.schemas import WhoAmI
from tuttitrip.mcp.services import tool_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.auth.services.token_verifier import (
    InvalidTokenError,
)
from tuttitrip.shared.auth.services.token_verifier import (
    TokenVerifier as Auth0TokenVerifier,
)
from tuttitrip.shared.config.settings import Settings
from tuttitrip.shared.pagination.schemas import MAX_SIZE, Page, PageParams
from tuttitrip.shared.permissions.logic.resolution import EffectivePermissions
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripRead
from tuttitrip.trips.services.trip_service import TripNotFoundError

MCP_PATH = "/mcp"
SERVER_NAME = "TuttiTrip"
INSTRUCTIONS = (
    "TuttiTrip plans group and family trips fairly. These tools read the "
    "signed-in user's own trips; they never show other people's trips."
)
TRIP_NOT_FOUND = "Trip not found"

_USER_KEY = "tuttitrip_user"
_PERMISSIONS_KEY = "tuttitrip_permissions"


# --- authentication ----------------------------------------------------------


class Auth0McpVerifier(TokenVerifier):
    """FastMCP token verifier that delegates to the app's Auth0 verification."""

    def __init__(self, core: Auth0TokenVerifier, base_url: str) -> None:
        super().__init__(base_url=base_url)
        self._core = core

    @override
    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate signature, issuer, audience and expiry.

        Args:
            token: The raw bearer token.

        Returns:
            The token with the caller's identity, or None (401) when invalid.
        """
        try:
            # Blocking JWKS fetch: keep it off the event loop.
            user = await asyncio.to_thread(self._core.verify, token)
        except InvalidTokenError:
            return None
        return AccessToken(
            token=token,
            client_id=user.sub,
            scopes=user.scopes,
            claims={_USER_KEY: user.model_dump()},
        )


def _user_of(token: AccessToken) -> AuthenticatedUser:
    return AuthenticatedUser.model_validate(token.claims[_USER_KEY])


async def _permissions_of(token: AccessToken) -> EffectivePermissions:
    """The caller's permissions, loaded once per request.

    FastMCP hands every check of one request the same token object, so the
    result is cached on it. It is never cached across requests.

    Args:
        token: The verified token of this request.

    Returns:
        Effective permissions read from the database.
    """
    cached = token.claims.get(_PERMISSIONS_KEY)
    if isinstance(cached, EffectivePermissions):
        return cached
    async with tool_service.open_session() as session:
        loaded = await tool_service.load_permissions(session, _user_of(token))
    token.claims[_PERMISSIONS_KEY] = loaded
    return loaded


# --- authorization -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class McpRequirement:
    """Tool guard: ``mcp:READ`` plus ``level`` on ``feature``.

    A tool without it is hidden from ``tools/list`` and refused on call.
    """

    feature: Feature
    level: Access

    async def __call__(self, context: AuthContext) -> bool:
        """Decide for one tool.

        Args:
            context: The token and the tool being listed or called.

        Returns:
            True when the caller holds both permissions.
        """
        if context.token is None:
            return False
        permissions = await _permissions_of(context.token)
        return permissions.allows(Feature.MCP, Access.READ) and permissions.allows(
            self.feature, self.level
        )


def mcp_requires(feature: Feature, level: Access) -> McpRequirement:
    """Guard for ``@mcp.tool(auth=...)``: exactly one per tool (a test checks it).

    Args:
        feature: A registry leaf the tool's data belongs to.
        level: ``READ`` or ``WRITE``.

    Returns:
        The check to pass as ``auth=``.
    """
    return McpRequirement(feature, level)


def _caller() -> tuple[AccessToken, AuthenticatedUser]:
    token = get_access_token()
    if token is None:  # unreachable behind the auth middleware
        msg = "Not authenticated"
        raise ToolError(msg)
    return token, _user_of(token)


# --- server ------------------------------------------------------------------


def create_mcp(
    settings: Settings, verifier: Auth0TokenVerifier | None = None
) -> FastMCP:
    """Build the server with its auth provider and tools.

    Args:
        settings: Settings (Auth0 tenant and ``mcp``).
        verifier: Token verifier override (tests); defaults to the tenant's.

    Returns:
        The FastMCP server; ``create_mcp_app`` turns it into an ASGI app.
    """
    auth0 = settings.auth0
    resource_url = settings.mcp.resource_url
    base_url = resource_url.removesuffix(MCP_PATH)
    core = verifier or Auth0TokenVerifier(
        domain=auth0.domain, audience=resource_url, roles_claim=auth0.roles_claim
    )
    server = FastMCP(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        auth=RemoteAuthProvider(
            token_verifier=Auth0McpVerifier(core, base_url),
            authorization_servers=[AnyHttpUrl(core.issuer)],
            base_url=base_url,
            resource_name=SERVER_NAME,
        ),
    )

    @server.tool(auth=mcp_requires(Feature.MCP, Access.READ))
    async def whoami() -> WhoAmI:
        """Say who is signed in and what they may use.

        Returns:
            Identity and the permission map.
        """
        token, user = _caller()
        return tool_service.whoami(user, await _permissions_of(token))

    @server.tool(auth=mcp_requires(Feature.TRIPS_CORE, Access.READ))
    async def list_trips(
        page: Annotated[int, Field(ge=1, description="Page number, from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=MAX_SIZE)] = 20,
    ) -> Page[TripRead]:
        """List the signed-in user's trips, newest first, with their role on each.

        Args:
            page: Page number, from 1.
            size: Trips per page.

        Returns:
            One page of trips.
        """
        _, user = _caller()
        async with tool_service.open_session() as session:
            return await tool_service.list_trips(
                session, user, PageParams(page=page, size=size)
            )

    @server.tool(auth=mcp_requires(Feature.TRIPS_CORE, Access.READ))
    async def get_trip(trip_id: UUID) -> TripRead:
        """Read one trip the signed-in user is a member of.

        Args:
            trip_id: Id of the trip, from list_trips.

        Returns:
            The trip with the user's role.

        Raises:
            ToolError: The trip does not exist or the user is not on it.
        """
        _, user = _caller()
        async with tool_service.open_session() as session:
            try:
                return await tool_service.get_trip(session, user, trip_id)
            except TripNotFoundError as exc:
                raise ToolError(TRIP_NOT_FOUND) from exc

    return server


class _McpEndpoint:
    """ASGI endpoint that hands requests for ``resource_url`` to the FastMCP app.

    The app serves ``MCP_PATH``; this adapter rewrites the path, so the public
    path is one exact route (no mount, no redirect, no catch-all under ``/api/v1``).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._app(
            {**scope, "path": MCP_PATH, "raw_path": MCP_PATH.encode()}, receive, send
        )


def create_mcp_app(
    settings: Settings, server: FastMCP | None = None
) -> tuple[StarletteWithLifespan, list[Route]]:
    """The FastMCP app (for its lifespan) and the routes to add at the app root.

    Args:
        settings: Settings (``mcp.resource_url``, ``mcp.allowed_hosts``, CORS origins).
        server: The server to wrap; defaults to ``create_mcp(settings)``.

    Returns:
        ``(app, routes)``: ``routes`` are the exact endpoint path of
        ``resource_url`` and the RFC 9728 metadata at
        ``/.well-known/oauth-protected-resource/<that path>``.
    """
    server = server or create_mcp(settings)
    app = server.http_app(
        path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        # Explicit host list = Host (and Origin) validation against DNS rebinding.
        # Browser clients are limited to the configured CORS origins; MCP
        # clients such as Claude and ChatGPT call from their servers.
        host_origin_protection="auto",
        allowed_hosts=[
            urlsplit(settings.mcp.resource_url).hostname or "",
            *settings.mcp.allowed_hosts,
        ],
        allowed_origins=settings.cors_origins,
    )
    auth = server.auth
    routes = [
        Route(urlsplit(settings.mcp.resource_url).path, _McpEndpoint(app)),
        *(auth.get_well_known_routes(mcp_path=MCP_PATH) if auth else []),
    ]
    return app, routes
