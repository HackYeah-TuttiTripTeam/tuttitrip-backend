"""Async engine and session factory built from settings."""

import asyncio
from functools import lru_cache

from sqlalchemy import URL, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tuttitrip.shared.config.settings import DatabaseSettings, get_settings


def database_url(database: DatabaseSettings) -> URL:
    """Build the asyncpg connection URL.

    Args:
        database: Connection parameters.

    Returns:
        A SQLAlchemy URL for the ``postgresql+asyncpg`` dialect.
    """
    return URL.create(
        "postgresql+asyncpg",
        username=database.user,
        password=database.password.get_secret_value(),
        host=database.host,
        port=database.port,
        database=database.name,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Create the process-wide async engine lazily.

    Returns:
        The cached engine.
    """
    database = get_settings().database
    return create_async_engine(
        database_url(database), echo=database.echo, pool_pre_ping=True
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Create the process-wide session factory lazily.

    Returns:
        The cached session factory.
    """
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    """Close pooled connections if the engine was ever created."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()


async def ping(timeout_seconds: float = 2.0) -> bool:
    """Check that the database answers a trivial query.

    Args:
        timeout_seconds: Upper bound for connecting and querying.

    Returns:
        True if ``SELECT 1`` succeeded in time.
    """
    try:
        async with asyncio.timeout(timeout_seconds), get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OSError, SQLAlchemyError, TimeoutError:
        return False
    return True
