"""Queries of the parameter versions."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.parameters.models import ParameterVersion
from tuttitrip.planning.parameters.schemas import ParametersQuery
from tuttitrip.shared.db.pagination import paginate
from tuttitrip.shared.pagination.schemas import Page

# A bigint key apart from other users of advisory locks ("tuttipar").
LOCK_KEY = 0x7475747469_706172


async def select_latest(session: AsyncSession) -> ParameterVersion | None:
    """Newest version.

    Args:
        session: Open session.

    Returns:
        The row, or None when no version was stored.
    """
    return await session.scalar(
        select(ParameterVersion).order_by(ParameterVersion.version.desc()).limit(1)
    )


async def insert_version(
    session: AsyncSession, values: dict[str, object], note: str | None, sub: str
) -> ParameterVersion:
    """Store the next version (serialised by an advisory lock).

    Args:
        session: Open session; the caller commits.
        values: The full set of parameters.
        note: Why it changed.
        sub: The administrator.

    Returns:
        The new row.
    """
    await session.execute(select(func.pg_advisory_xact_lock(LOCK_KEY)))
    latest = await session.scalar(select(func.max(ParameterVersion.version)))
    row = ParameterVersion(
        version=(latest or 0) + 1, values=values, note=note, created_by_sub=sub
    )
    session.add(row)
    await session.flush()
    return row


async def select_page(
    session: AsyncSession, query: ParametersQuery
) -> Page[ParameterVersion]:
    """One page of the history.

    Args:
        session: Open session.
        query: Page and direction.

    Returns:
        The page, by version.
    """
    return await paginate(
        session, select(ParameterVersion), query, (ParameterVersion.version,)
    )
