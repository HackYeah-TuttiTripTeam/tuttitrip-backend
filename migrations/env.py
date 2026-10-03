"""Alembic environment: async engine, URL and metadata from the app.

Every ``tuttitrip.**.models`` module is imported automatically, so a new
domain's tables are picked up by ``alembic revision --autogenerate`` without
editing this file.
"""

import asyncio
import importlib
import pkgutil

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import tuttitrip
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.base import Base
from tuttitrip.shared.db.session import database_url


def import_all_models() -> None:
    """Import every ``models`` module so its tables join ``Base.metadata``."""
    for module in pkgutil.walk_packages(tuttitrip.__path__, prefix="tuttitrip."):
        if module.name.rsplit(".", 1)[-1] == "models":
            importlib.import_module(module.name)


import_all_models()
target_metadata = Base.metadata
url = database_url(get_settings().database)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (``alembic upgrade --sql``)."""
    context.configure(
        url=url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations on an open (sync-facade) connection.

    Args:
        connection: Connection provided by ``AsyncConnection.run_sync``.
    """
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Connect with asyncpg and run migrations."""
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
