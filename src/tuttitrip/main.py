"""Composition root: builds the FastAPI app and registers every domain router.

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
from tuttitrip.planning.api import router as planning_router
from tuttitrip.planning.fairness.api import router as fairness_router
from tuttitrip.planning.linter.api import router as linter_router
from tuttitrip.profiles.api import router as profiles_router
from tuttitrip.shared.auth.api import router as auth_router
from tuttitrip.shared.config.settings import Settings, get_settings
from tuttitrip.shared.db.session import dispose_engine
from tuttitrip.shared.health.api import router as health_router
from tuttitrip.trips.api import router as trips_router

# Every `api.py` router must be listed here (a test checks it).
ROUTERS: tuple[APIRouter, ...] = (
    health_router,
    auth_router,
    trips_router,
    profiles_router,
    interview_router,
    planning_router,
    fairness_router,
    linter_router,
    accommodation_router,
    expenses_router,
    settlement_router,
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
    app = FastAPI(title="TuttiTrip API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
