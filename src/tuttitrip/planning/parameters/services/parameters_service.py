"""Read and store versions of the algorithm parameters.

Version 0 is ``DEFAULT_PARAMS`` (no row). Planning reads the newest version
once per plan and records its number with the plan.
"""

from dataclasses import asdict
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.parameters import db
from tuttitrip.planning.parameters.models import ParameterVersion
from tuttitrip.planning.parameters.schemas import (
    ParametersCreate,
    ParametersQuery,
    ParametersRead,
)
from tuttitrip.shared.pagination.schemas import Page

DEFAULT_VERSION = 0


class CurrentParams(NamedTuple):
    """The parameters in force and their version."""

    version: int
    params: AlgorithmParams


def _read(row: ParameterVersion) -> ParametersRead:
    return ParametersRead(
        version=row.version,
        values=AlgorithmParams(**row.values),
        note=row.note,
        created_by_sub=row.created_by_sub,
        created_at=row.created_at,
    )


async def current(session: AsyncSession) -> CurrentParams:
    """The parameters a new plan is computed with.

    Args:
        session: Open session.

    Returns:
        The newest stored version, or the built-in defaults as version 0.
    """
    row = await db.select_latest(session)
    if row is None:
        return CurrentParams(DEFAULT_VERSION, DEFAULT_PARAMS)
    return CurrentParams(row.version, AlgorithmParams(**row.values))


async def read_current(session: AsyncSession) -> ParametersRead:
    """The newest version as the admin sees it.

    Args:
        session: Open session.

    Returns:
        The version, or version 0 with the defaults.
    """
    row = await db.select_latest(session)
    if row is None:
        return ParametersRead(version=DEFAULT_VERSION, values=DEFAULT_PARAMS)
    return _read(row)


async def default_alpha(session: AsyncSession) -> float:
    """The fairness slider a new trip starts with (set by the administrator).

    Args:
        session: Open session.

    Returns:
        ``alpha`` of the current version.
    """
    return (await current(session)).params.alpha


async def create_version(
    session: AsyncSession, sub: str, data: ParametersCreate
) -> ParametersRead:
    """Store a new version; old versions and the plans made with them stay.

    Args:
        session: Open session.
        sub: The administrator.
        data: The full set of parameters (already range-checked) and a note.

    Returns:
        The stored version.
    """
    row = await db.insert_version(session, asdict(data.values), data.note, sub)
    await session.commit()
    return _read(row)


async def list_versions(
    session: AsyncSession, query: ParametersQuery
) -> Page[ParametersRead]:
    """The history of versions.

    Args:
        session: Open session.
        query: Page and direction.

    Returns:
        One page, newest first by default.
    """
    page = await db.select_page(session, query)
    return Page[ParametersRead].of([_read(r) for r in page.items], page.total, query)
