"""Location queries on PostgreSQL."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, delete, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.db.pagination import ordering, paginate
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.locations.models import TripLocation, TripLocationConsent
from tuttitrip.trips.locations.schemas import (
    LocationFilters,
    LocationQuery,
    LocationSort,
)

_SORT = {LocationSort.RECORDED_AT: TripLocation.recorded_at}


def _visible(trip_id: UUID, now: datetime) -> Select[TripLocation]:
    """Positions that are neither expired nor without a live consent.

    Args:
        trip_id: Trip id.
        now: Current time.

    Returns:
        A select of valid positions of the trip.
    """
    return select(TripLocation).where(
        TripLocation.trip_id == trip_id,
        TripLocation.expires_at > now,
        exists().where(
            TripLocationConsent.trip_id == TripLocation.trip_id,
            TripLocationConsent.profile_id == TripLocation.profile_id,
            TripLocationConsent.until > now,
        ),
    )


def _apply_filters(
    stmt: Select[TripLocation], filters: LocationFilters, profile_id: UUID | None
) -> Select[TripLocation]:
    if filters.mine is True:
        stmt = stmt.where(TripLocation.profile_id == profile_id)
    elif filters.mine is False:
        stmt = stmt.where(TripLocation.profile_id != profile_id)
    return stmt


async def select_locations(
    session: AsyncSession,
    trip_id: UUID,
    profile_id: UUID | None,
    now: datetime,
    query: LocationQuery,
) -> Page[TripLocation]:
    """One page of the trip's valid positions.

    Args:
        session: Open session.
        trip_id: Trip id.
        profile_id: The caller's profile (for the ``mine`` filter).
        now: Current time; expired positions and lapsed consents are skipped.
        query: Page, sort, direction and filters.

    Returns:
        The page of rows.
    """
    order = ordering(_SORT, query.sort, TripLocation.profile_id)
    stmt = _apply_filters(_visible(trip_id, now), query, profile_id)
    return await paginate(session, stmt, query, order)


async def select_active_consent(
    session: AsyncSession, trip_id: UUID, profile_id: UUID, now: datetime
) -> TripLocationConsent | None:
    """The person's consent, if it has not lapsed.

    Args:
        session: Open session.
        trip_id: Trip id.
        profile_id: The person's profile.
        now: Current time.

    Returns:
        The consent, or None.
    """
    return await session.scalar(
        select(TripLocationConsent).where(
            TripLocationConsent.trip_id == trip_id,
            TripLocationConsent.profile_id == profile_id,
            TripLocationConsent.until > now,
        )
    )


async def upsert_consent(
    session: AsyncSession, trip_id: UUID, profile_id: UUID, until: datetime
) -> TripLocationConsent:
    """Grant or extend the consent and flush.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        profile_id: The person's profile.
        until: When the consent lapses.

    Returns:
        The stored row.
    """
    stmt = (
        insert(TripLocationConsent)
        .values(trip_id=trip_id, profile_id=profile_id, until=until)
        .on_conflict_do_update(
            index_elements=[
                TripLocationConsent.trip_id,
                TripLocationConsent.profile_id,
            ],
            set_={"until": until},
        )
        .returning(TripLocationConsent)
    )
    return (
        await session.scalars(stmt, execution_options={"populate_existing": True})
    ).one()


async def upsert_position(  # ruff: ignore[too-many-arguments]
    session: AsyncSession,
    trip_id: UUID,
    profile_id: UUID,
    *,
    latitude: float,
    longitude: float,
    accuracy_m: float | None,
    now: datetime,
    expires_at: datetime,
) -> TripLocation:
    """Overwrite the person's last position and flush.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        profile_id: The person's profile.
        latitude: Degrees.
        longitude: Degrees.
        accuracy_m: Radius in metres, if known.
        now: Recording time.
        expires_at: When the position stops being returned.

    Returns:
        The stored row.
    """
    values = {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy_m": accuracy_m,
        "recorded_at": now,
        "expires_at": expires_at,
    }
    stmt = (
        insert(TripLocation)
        .values(trip_id=trip_id, profile_id=profile_id, **values)
        .on_conflict_do_update(
            index_elements=[TripLocation.trip_id, TripLocation.profile_id], set_=values
        )
        .returning(TripLocation)
    )
    return (
        await session.scalars(stmt, execution_options={"populate_existing": True})
    ).one()


async def delete_for_profile(
    session: AsyncSession, trip_id: UUID, profile_id: UUID
) -> None:
    """Delete the person's consent and position.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        profile_id: The person's profile.
    """
    for model in (TripLocation, TripLocationConsent):
        await session.execute(
            delete(model).where(
                model.trip_id == trip_id, model.profile_id == profile_id
            )
        )


async def purge_expired(session: AsyncSession, trip_id: UUID, now: datetime) -> None:
    """Delete lapsed consents and every position that expired or lost its consent.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        now: Current time.
    """
    await session.execute(
        delete(TripLocationConsent).where(
            TripLocationConsent.trip_id == trip_id, TripLocationConsent.until <= now
        )
    )
    has_consent = exists().where(
        TripLocationConsent.trip_id == TripLocation.trip_id,
        TripLocationConsent.profile_id == TripLocation.profile_id,
    )
    await session.execute(
        delete(TripLocation).where(
            TripLocation.trip_id == trip_id,
            or_(TripLocation.expires_at <= now, ~has_consent),
        )
    )


async def delete_for_profiles(
    session: AsyncSession, profile_ids: Sequence[UUID]
) -> int:
    """Delete the consents and positions of some profiles, on every trip.

    Args:
        session: Open session (caller commits).
        profile_ids: The profiles.

    Returns:
        How many consents were deleted.
    """
    await session.execute(
        delete(TripLocation).where(TripLocation.profile_id.in_(profile_ids))
    )
    result = await session.execute(
        delete(TripLocationConsent)
        .where(TripLocationConsent.profile_id.in_(profile_ids))
        .returning(TripLocationConsent.profile_id)
    )
    return len(result.all())
