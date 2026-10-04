"""Locations: members opt in, push their last position, and see each other's.

Privacy rules, all enforced here and in ``db``:

- Sharing is off until the person turns it on, per trip, for a limited time
  (5 minutes to 24 hours). A position sent without a live consent is refused
  and never stored.
- Only the latest position is kept (overwritten on every update), with an
  expiry (setting ``locations.position_ttl_minutes``, 15 minutes by default).
  Nothing is returned after it, and expired rows, lapsed consents and
  positions without a consent are deleted on the next access to the trip's
  locations.
- Stopping deletes the consent and the position at once; leaving the trip
  does the same (``clear_profile``).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.locations import db
from tuttitrip.trips.locations.models import TripLocation
from tuttitrip.trips.locations.schemas import (
    ConsentRead,
    ConsentUpdate,
    LocationQuery,
    LocationRead,
    PositionUpdate,
)
from tuttitrip.trips.schemas import TripMembership


class LocationProfileMissingError(Exception):
    """The caller has no profile on the trip, so there is nobody to locate."""


class SharingOffError(Exception):
    """The caller has not turned sharing on (or it lapsed)."""


async def _my_profile(session: AsyncSession, membership: TripMembership) -> UUID:
    profile_id = await profile_service.find_account_profile(
        session, membership.trip_id, membership.sub
    )
    if profile_id is None:
        raise LocationProfileMissingError(str(membership.trip_id))
    return profile_id


def _read(row: TripLocation, name: str, *, is_me: bool) -> LocationRead:
    return LocationRead(
        profile_id=row.profile_id,
        display_name=name,
        latitude=row.latitude,
        longitude=row.longitude,
        accuracy_m=row.accuracy_m,
        recorded_at=row.recorded_at,
        expires_at=row.expires_at,
        is_me=is_me,
    )


async def get_consent(session: AsyncSession, membership: TripMembership) -> ConsentRead:
    """Whether the caller shares their location right now.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).

    Returns:
        The state; ``until`` is null when off.

    Raises:
        LocationProfileMissingError: The caller has no profile on the trip.
    """
    profile_id = await _my_profile(session, membership)
    consent = await db.select_active_consent(
        session, membership.trip_id, profile_id, datetime.now(UTC)
    )
    return ConsentRead(
        enabled=consent is not None, until=None if consent is None else consent.until
    )


async def set_consent(
    session: AsyncSession, membership: TripMembership, data: ConsentUpdate
) -> ConsentRead:
    """Turn sharing on (or extend it) for ``duration_minutes`` and commit.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        data: How long to share.

    Returns:
        The new state.

    Raises:
        LocationProfileMissingError: The caller has no profile on the trip.
    """
    profile_id = await _my_profile(session, membership)
    until = datetime.now(UTC) + timedelta(minutes=data.duration_minutes)
    await db.upsert_consent(session, membership.trip_id, profile_id, until)
    await session.commit()
    return ConsentRead(enabled=True, until=until)


async def stop_sharing(session: AsyncSession, membership: TripMembership) -> None:
    """Turn sharing off: delete the caller's consent and position, and commit.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).

    Raises:
        LocationProfileMissingError: The caller has no profile on the trip.
    """
    profile_id = await _my_profile(session, membership)
    await db.delete_for_profile(session, membership.trip_id, profile_id)
    await session.commit()


async def clear_profile(session: AsyncSession, trip_id: UUID, profile_id: UUID) -> None:
    """Forget a person's consent and position (they left the trip). No commit.

    Args:
        session: Open session (caller commits).
        trip_id: Trip id.
        profile_id: The person's profile.
    """
    await db.delete_for_profile(session, trip_id, profile_id)


async def update_position(
    session: AsyncSession, membership: TripMembership, data: PositionUpdate
) -> LocationRead:
    """Store the caller's latest position, if they share, and commit.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        data: The position.

    Returns:
        The stored position.

    Raises:
        LocationProfileMissingError: The caller has no profile on the trip.
        SharingOffError: No live consent; nothing is stored.
    """
    profile_id = await _my_profile(session, membership)
    now = datetime.now(UTC)
    consent = await db.select_active_consent(
        session, membership.trip_id, profile_id, now
    )
    if consent is None:
        await db.purge_expired(session, membership.trip_id, now)
        await session.commit()
        raise SharingOffError(str(membership.trip_id))
    ttl = timedelta(minutes=get_settings().locations.position_ttl_minutes)
    row = await db.upsert_position(
        session,
        membership.trip_id,
        profile_id,
        latitude=data.latitude,
        longitude=data.longitude,
        accuracy_m=data.accuracy_m,
        now=now,
        expires_at=min(now + ttl, consent.until),
    )
    await session.commit()
    profile = await profile_service.get_profile(session, membership, profile_id)
    return _read(row, profile.display_name, is_me=True)


async def list_locations(
    session: AsyncSession, membership: TripMembership, query: LocationQuery
) -> Page[LocationRead]:
    """One page of the positions members currently share.

    Expired positions and lapsed consents are deleted first and never returned.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        query: Page, sort, direction and filters.

    Returns:
        The page.
    """
    now = datetime.now(UTC)
    await db.purge_expired(session, membership.trip_id, now)
    await session.commit()
    profiles = {
        p.id: p for p in await profile_service.list_profiles(session, membership)
    }
    mine = next((p.id for p in profiles.values() if p.user_sub == membership.sub), None)
    page = await db.select_locations(session, membership.trip_id, mine, now, query)
    return Page[LocationRead](
        items=[
            _read(
                row, profiles[row.profile_id].display_name, is_me=row.profile_id == mine
            )
            for row in page.items
            if row.profile_id in profiles
        ],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )
