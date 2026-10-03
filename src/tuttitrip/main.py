"""Composition root: builds the FastAPI app and registers every domain router.

Every endpoint, including the OpenAPI document and the docs, is served under
``API_PREFIX`` (``/api/v1``); ``tests/architecture/test_routes.py`` enforces it.
Run with ``uvicorn tuttitrip.main:app``.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tuttitrip.accommodation.api import router as accommodation_router
from tuttitrip.expenses.api import router as expenses_router
from tuttitrip.expenses.settlement.api import router as settlement_router
from tuttitrip.interview.api import router as interview_router
from tuttitrip.places.api import router as places_router
from tuttitrip.planning.api import router as planning_router
from tuttitrip.planning.fairness.api import router as fairness_router
from tuttitrip.planning.linter.api import router as linter_router
from tuttitrip.planning.plans.api import router as plans_router
from tuttitrip.profiles.api import router as profiles_router
from tuttitrip.profiles.feedback.api import router as feedback_router
from tuttitrip.search.api import router as search_router
from tuttitrip.shared.config.settings import Settings, get_settings
from tuttitrip.shared.db.session import dispose_engine
from tuttitrip.shared.errors.api import register_error_handlers
from tuttitrip.shared.health.api import router as health_router
from tuttitrip.shared.jobs.api import router as jobs_router
from tuttitrip.shared.permissions.api import document_permissions
from tuttitrip.shared.permissions.api import router as permissions_router
from tuttitrip.trips.api import router as trips_router

# Bump the version only for a breaking change that needs both APIs side by side.
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

# Every `api.py` router must be listed here (a test checks it).
ROUTERS: tuple[APIRouter, ...] = (
    health_router,
    permissions_router,
    jobs_router,
    trips_router,
    profiles_router,
    feedback_router,
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
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Release database connections on shutdown.

    Args:
        _app: The application (unused).

    Yields:
        Control while the app is running.
    """
    try:
        yield
    finally:
        await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Settings override (tests); defaults to the environment.

    Returns:
        The configured FastAPI app.
    """
    settings = settings or get_settings()
    app = FastAPI(
        title="TuttiTrip API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
        swagger_ui_oauth2_redirect_url=f"{API_PREFIX}/docs/oauth2-redirect",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex or None,
        # Bearer tokens only: the API reads no cookies, so no credentialed CORS.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api = APIRouter(prefix=API_PREFIX)
    register_error_handlers(app)
    for router in ROUTERS:
        api.include_router(router)
    app.include_router(api)
    document_permissions(app)
    return app


app = create_app()
