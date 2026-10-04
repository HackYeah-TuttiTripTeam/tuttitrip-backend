"""Accommodation queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.accommodation.models import (
    AccommodationOffer,
    AccommodationRequirement,
    RequirementsVersion,
    SearchOpening,
)
from tuttitrip.accommodation.schemas import OpeningQuery, OpeningSort
from tuttitrip.shared.db.pagination import ordering, paginate
from tuttitrip.shared.pagination.schemas import Page


async def select_requirements(
    session: AsyncSession, trip_id: UUID
) -> Sequence[AccommodationRequirement]:
    """Requirements of a trip in a stable order.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Rows ordered by kind and key.
    """
    result = await session.scalars(
        select(AccommodationRequirement)
        .where(AccommodationRequirement.trip_id == trip_id)
        .order_by(AccommodationRequirement.kind, AccommodationRequirement.key)
    )
    return result.all()


async def select_version(session: AsyncSession, trip_id: UUID) -> int:
    """Change counter of the trip's requirements.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The version, 0 when requirements were never saved.
    """
    version = await session.scalar(
        select(RequirementsVersion.version).where(
            RequirementsVersion.trip_id == trip_id
        )
    )
    return version or 0


async def replace_requirements(
    session: AsyncSession, trip_id: UUID, rows: Sequence[AccommodationRequirement]
) -> None:
    """Replace all requirements of a trip.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        rows: New requirements (``trip_id`` already set).
    """
    await session.execute(
        delete(AccommodationRequirement).where(
            AccommodationRequirement.trip_id == trip_id
        )
    )
    session.add_all(rows)
    await session.flush()


async def bump_version(session: AsyncSession, trip_id: UUID) -> None:
    """Increment the requirements version (creating it at 1).

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
    """
    upsert = insert(RequirementsVersion).values(trip_id=trip_id, version=1)
    await session.execute(
        upsert.on_conflict_do_update(
            index_elements=[RequirementsVersion.trip_id],
            set_={"version": RequirementsVersion.version + 1},
        )
    )


_OPENING_SORT = {OpeningSort.OPENED_AT: SearchOpening.opened_at}


async def insert_opening(session: AsyncSession, opening: SearchOpening) -> None:
    """Append a row to the openings log.

    Args:
        session: Open session (caller commits).
        opening: The new row.
    """
    session.add(opening)
    await session.flush()
    await session.refresh(opening)


async def select_openings(
    session: AsyncSession, trip_id: UUID, query: OpeningQuery
) -> Page[SearchOpening]:
    """One page of a trip's openings log.

    Args:
        session: Open session.
        trip_id: Trip id.
        query: Page, direction, sort and filters.

    Returns:
        The page of rows.
    """
    stmt = select(SearchOpening).where(SearchOpening.trip_id == trip_id)
    if query.platform is not None:
        stmt = stmt.where(SearchOpening.platform == query.platform)
    order = ordering(_OPENING_SORT, query.sort, SearchOpening.id)
    return await paginate(session, stmt, query, order)


async def insert_offer(session: AsyncSession, offer: AccommodationOffer) -> None:
    """Add a pasted offer and load its server defaults.

    Args:
        session: Open session (caller commits).
        offer: The new row.
    """
    session.add(offer)
    await session.flush()
    await session.refresh(offer)


async def select_offer(
    session: AsyncSession, trip_id: UUID, offer_id: UUID
) -> AccommodationOffer | None:
    """One offer of a trip.

    Args:
        session: Open session.
        trip_id: Trip id (an offer of another trip is not found).
        offer_id: Offer id.

    Returns:
        The row, or None.
    """
    return await session.scalar(
        select(AccommodationOffer).where(
            AccommodationOffer.trip_id == trip_id, AccommodationOffer.id == offer_id
        )
    )
