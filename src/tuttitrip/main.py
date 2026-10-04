"""Composition root: builds the FastAPI app and registers every domain router.

Every endpoint, including the OpenAPI document and the docs, is served under
``API_PREFIX`` (``/api/v1``); ``tests/architecture/test_routes.py`` enforces it.
Run with ``uvicorn tuttitrip.main:app``.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastmcp.utilities.lifespan import combine_lifespans

from tuttitrip.accommodation.api import router as accommodation_router
from tuttitrip.accounts.api import router as accounts_router
from tuttitrip.demo.api import internal_router as demo_internal_router
from tuttitrip.demo.api import router as demo_router
from tuttitrip.expenses.api import router as expenses_router
from tuttitrip.expenses.settlement.api import router as settlement_router
from tuttitrip.interview.api import router as interview_router
from tuttitrip.mcp.api import create_mcp_app
from tuttitrip.notifications.api import router as notifications_router
from tuttitrip.notifications.services import notification_service
from tuttitrip.places.api import router as places_router
from tuttitrip.planning.api import router as planning_router
from tuttitrip.planning.fairness.api import router as fairness_router
from tuttitrip.planning.linter.api import router as linter_router
from tuttitrip.planning.plans.api import router as plans_router
from tuttitrip.profiles.api import router as profiles_router
from tuttitrip.profiles.feedback.api import router as feedback_router
from tuttitrip.profiles.preferences.api import router as preferences_router
from tuttitrip.search.api import router as search_router
from tuttitrip.shared.admin_users.api import router as admin_users_router
from tuttitrip.shared.admin_users.services import erasure
from tuttitrip.shared.config.settings import Settings, get_settings
from tuttitrip.shared.db.session import dispose_engine
from tuttitrip.shared.errors.api import register_error_handlers
from tuttitrip.shared.health.api import router as health_router
from tuttitrip.shared.jobs.api import router as jobs_router
from tuttitrip.shared.permissions.api import document_permissions
from tuttitrip.shared.permissions.api import router as permissions_router
from tuttitrip.trips.api import router as trips_router
from tuttitrip.trips.checkins.api import router as checkins_router
from tuttitrip.trips.invitations.api import router as invitations_router
from tuttitrip.trips.invitations.services import invitation_service
from tuttitrip.trips.photos.api import router as photos_router
from tuttitrip.trips.services import trip_service
from tuttitrip.voting.api import router as voting_router

# Bump the version only for a breaking change that needs both APIs side by side.
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

log = logging.getLogger(__name__)

# Every `api.py` router must be listed here (a test checks it).
ROUTERS: tuple[APIRouter, ...] = (
    health_router,
    permissions_router,
    admin_users_router,
    accounts_router,
    jobs_router,
    demo_router,
    demo_internal_router,
    trips_router,
    invitations_router,
    checkins_router,
    voting_router,
    photos_router,
    profiles_router,
    feedback_router,
    preferences_router,
    interview_router,
    planning_router,
    fairness_router,
    plans_router,
    linter_router,
    accommodation_router,
    expenses_router,
    settlement_router,
    search_router,
    places_router,
    notifications_router,
)

# Domain data cleared when an administrator deletes an account.
erasure.register(trip_service.erase_account)
erasure.register(invitation_service.erase_account)
erasure.register(notification_service.erase_account)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Close the notification streams and release database connections on shutdown.

    Args:
        app: The application.

    Yields:
        Control while the app is running.
    """
    if not get_settings().admin.protected_discord_ids:
        log.warning(
            "TUTTITRIP_ADMIN__PROTECTED_DISCORD_IDS is empty: superadmin "
            "accounts are not protected from being blocked or deleted"
        )
    try:
        yield
    finally:
        hub = getattr(app.state, "notification_hub", None)
        if hub is not None:
            await hub.stop()
        await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Settings override (tests); defaults to the environment.

    Returns:
        The configured FastAPI app.
    """
    settings = settings or get_settings()
    mcp_app, mcp_routes = (
        create_mcp_app(settings) if settings.mcp.enabled else (None, [])
    )
    app = FastAPI(
        title="TuttiTrip API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
        swagger_ui_oauth2_redirect_url=f"{API_PREFIX}/docs/oauth2-redirect",
        lifespan=combine_lifespans(lifespan, mcp_app.lifespan) if mcp_app else lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex or None,
        # Bearer tokens only: the API reads no cookies, so no credentialed CORS.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        # MCP clients read the OAuth challenge.
        expose_headers=["WWW-Authenticate"],
    )
    api = APIRouter(prefix=API_PREFIX)
    register_error_handlers(app)
    for router in ROUTERS:
        api.include_router(router)
    app.include_router(api)
    if mcp_app is not None:
        # Two exact routes after the routers (no mount, so REST keeps its 405s and
        # redirects): the MCP endpoint and its RFC 9728 metadata at the root.
        app.router.routes.extend(mcp_routes)
    document_permissions(app)
    return app


app = create_app()
