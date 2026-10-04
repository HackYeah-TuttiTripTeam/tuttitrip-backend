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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from tuttitrip.mcp import schemas as constants
from tuttitrip.mcp.schemas import McpFairness, McpPlan, VetoResult, WhoAmI
from tuttitrip.mcp.services import tool_service
from tuttitrip.mcp.services.tool_service import ToolCall, ToolFailedError
from tuttitrip.planning.linter.schemas import LintReport, NamedPlan
from tuttitrip.profiles.feedback.schemas import RatingRead, RatingValue, ReasonCode
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
TRIP_NOT_FOUND = constants.TRIP_NOT_FOUND

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
"""Tools that only read: ChatGPT does not ask the user to confirm them."""
RATING = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
"""Setting a rating again changes nothing."""
VETO = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)
"""A veto is final and recomputes the plan."""

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


@asynccontextmanager
async def _tool(name: str, trip_id: UUID | None = None) -> AsyncGenerator[ToolCall]:
    """Session and caller for one tool call; refusals become ``ToolError``.

    Args:
        name: Tool name, for the call log.
        trip_id: The trip the tool works on, for the call log.

    Yields:
        An open session and the caller.

    Raises:
        ToolError: The service refused (``ToolFailedError``); only its message.
    """
    _, user = _caller()
    try:
        async with tool_service.tool_call(name, user, trip_id) as call:
            yield call
    except ToolFailedError as exc:
        raise ToolError(str(exc)) from exc


# --- server ------------------------------------------------------------------


def _add_trip_tools(server: FastMCP) -> None:
    """Tools for the caller and their trips.

    Args:
        server: The server to add them to.
    """

    @server.tool(auth=mcp_requires(Feature.MCP, Access.READ), annotations=READ_ONLY)
    async def whoami() -> WhoAmI:
        """Say who is signed in and what they may use.

        Returns:
            Identity and the permission map.
        """
        token, user = _caller()
        return tool_service.whoami(user, await _permissions_of(token))

    @server.tool(
        auth=mcp_requires(Feature.TRIPS_CORE, Access.READ), annotations=READ_ONLY
    )
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

    @server.tool(
        auth=mcp_requires(Feature.TRIPS_CORE, Access.READ), annotations=READ_ONLY
    )
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


def _add_plan_tools(server: FastMCP) -> None:
    """Read-only tools for the plan, the ledger and the linter.

    Args:
        server: The server to add them to.
    """

    @server.tool(
        auth=mcp_requires(Feature.PLANNING_PLANS, Access.READ), annotations=READ_ONLY
    )
    async def get_plan(
        trip_id: UUID,
        day: Annotated[
            int | None, Field(ge=1, description="Day number from 1; omit for all days.")
        ] = None,
    ) -> McpPlan:
        """Read the newest plan of a trip, day by day.

        Each stop has its hours, cost, source link and whether the price and the
        opening hours are verified. Ask for one `day` when the trip is long.

        Args:
            trip_id: Id of the trip, from list_trips.
            day: Day number from 1, or omitted for every day.

        Returns:
            The plan with the requested day or days.
        """
        async with _tool("get_plan", trip_id) as call:
            return await tool_service.get_plan(call.session, call.user, trip_id, day)

    @server.tool(
        auth=mcp_requires(Feature.PLANNING_FAIRNESS, Access.READ),
        annotations=READ_ONLY,
    )
    async def get_fairness(trip_id: UUID) -> McpFairness:
        """Read the fairness ledger of the newest plan.

        Per person: welfare `u`, what they would get alone `u_star`, `r` ("x% of
        their own maximum"), the floor and the five domains; for the group `min_r`
        and Jain's index. Also the guarantees that were missed and the conflicts.

        Args:
            trip_id: Id of the trip, from list_trips.

        Returns:
            The ledger.
        """
        async with _tool("get_fairness", trip_id) as call:
            return await tool_service.get_fairness(call.session, call.user, trip_id)

    @server.tool(
        auth=mcp_requires(Feature.PLANNING_LINTER, Access.READ), annotations=READ_ONLY
    )
    async def get_violations(trip_id: UUID) -> LintReport:
        """List the rule violations of the newest plan (opening hours, pace, budget...).

        Args:
            trip_id: Id of the trip, from list_trips.

        Returns:
            Every rule of the linter with its violations and warnings.
        """
        async with _tool("get_violations", trip_id) as call:
            return await tool_service.get_violations(call.session, call.user, trip_id)

    @server.tool(
        auth=mcp_requires(Feature.PLANNING_LINTER, Access.READ), annotations=READ_ONLY
    )
    async def lint_plan(trip_id: UUID, plan: NamedPlan) -> LintReport:
        """Check a plan from another tool against this trip's people and city.

        Split the plan into days (`day` as YYYY-MM-DD) and stops (`name`, `start`
        and `end` as HH:MM, local time). Names are matched to the city's catalog;
        a name that is not clearly one place is reported as an unknown place.
        Nothing is stored.

        Args:
            trip_id: Id of the trip, from list_trips.
            plan: The days and stops.

        Returns:
            Every rule of the linter with its violations and warnings.
        """
        async with _tool("lint_plan", trip_id) as call:
            return await tool_service.lint_plan(call.session, call.user, trip_id, plan)


def _add_write_tools(server: FastMCP) -> None:
    """Tools that change data (rate limited per user).

    Args:
        server: The server to add them to.
    """

    @server.tool(
        auth=mcp_requires(Feature.PROFILES_FEEDBACK, Access.WRITE),
        annotations=RATING,
    )
    async def rate_place(
        trip_id: UUID,
        place_id: UUID,
        rating: RatingValue,
        reason: Annotated[
            ReasonCode | None, Field(description="Required for dont_want only.")
        ] = None,
    ) -> RatingRead:
        """Set your own rating of a place; setting it again changes nothing.

        Args:
            trip_id: Id of the trip, from list_trips.
            place_id: Id of the catalog place.
            rating: want, neutral or dont_want.
            reason: Why, for dont_want.

        Returns:
            The stored rating.
        """
        async with _tool("rate_place", trip_id) as call:
            return await tool_service.rate_place(
                call.session, call.user, trip_id, place_id, rating, reason
            )

    @server.tool(
        auth=mcp_requires(Feature.PROFILES_FEEDBACK, Access.WRITE), annotations=VETO
    )
    async def veto_place(
        trip_id: UUID,
        place_id: UUID,
        on_behalf_of: Annotated[
            UUID | None,
            Field(description="Profile id of another person; host or co-host only."),
        ] = None,
    ) -> VetoResult:
        """File a veto on a place: it is final and the plan is recomputed without it.

        Args:
            trip_id: Id of the trip, from list_trips.
            place_id: Id of the catalog place.
            on_behalf_of: Profile id of another person (host or co-host only).

        Returns:
            The veto, the new plan version and the places that left the plan.
        """
        async with _tool("veto_place", trip_id) as call:
            return await tool_service.veto_place(
                call.session, call.user, trip_id, place_id, on_behalf_of
            )


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

    _add_trip_tools(server)
    _add_plan_tools(server)
    _add_write_tools(server)

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
