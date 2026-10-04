"""Check-ins: members tell the group where they stay.

Entries are personal data and are removed once the trip is over. There is no
background job: the first read or write after the end date deletes them, and
nothing is returned or accepted from that day on.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import ProfileNotFoundError
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.checkins import db
from tuttitrip.trips.checkins.logic import rules
from tuttitrip.trips.checkins.models import TripCheckin
from tuttitrip.trips.checkins.schemas import CheckinQuery, CheckinRead, CheckinUpdate
from tuttitrip.trips.schemas import TripMembership
from tuttitrip.trips.services import trip_service


class CheckinProfileNotFoundError(Exception):
    """The profile is not on this trip."""


class CheckinForbiddenError(Exception):
    """The caller may not change this person's check-in."""


class CheckinTripOverError(Exception):
    """The trip has ended; check-ins are gone."""


def _read(row: TripCheckin, profile: ProfileRead, caller_sub: str) -> CheckinRead:
    return CheckinRead(
        profile_id=row.profile_id,
        display_name=profile.display_name,
        accommodation=row.accommodation,
        room=row.room,
        updated_at=row.updated_at,
        is_me=profile.user_sub == caller_sub,
    )


async def _purge_if_over(session: AsyncSession, membership: TripMembership) -> bool:
    """Delete the trip's check-ins when the trip has ended.

    Returns:
        Whether the trip is over.
    """
    trip = await trip_service.get_trip(session, membership)
    if not rules.is_over(trip.end_date, datetime.now(UTC).date()):
        return False
    await db.delete_trip_checkins(session, membership.trip_id)
    await session.commit()
    return True


async def _editable_profile(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> ProfileRead:
    try:
        profile = await profile_service.get_profile(session, membership, profile_id)
    except ProfileNotFoundError as exc:
        raise CheckinProfileNotFoundError(str(profile_id)) from exc
    if not rules.can_edit(membership.role, membership.sub, profile.user_sub):
        msg = "Only the owner of the profile (the host for profiles without an account)"
        raise CheckinForbiddenError(msg)
    return profile


async def list_checkins(
    session: AsyncSession, membership: TripMembership, query: CheckinQuery
) -> Page[CheckinRead]:
    """One page of the trip's check-ins, with names from the profiles.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        query: Page, sort, direction and filters.

    Returns:
        The page; empty once the trip is over.
    """
    if await _purge_if_over(session, membership):
        return Page[CheckinRead].of([], 0, query)
    page = await db.select_checkins(session, membership.trip_id, query)
    profiles = {
        p.id: p for p in await profile_service.list_profiles(session, membership)
    }
    items = [
        _read(row, profiles[row.profile_id], membership.sub)
        for row in page.items
        if row.profile_id in profiles
    ]
    return Page[CheckinRead](
        items=items, total=page.total, page=page.page, size=page.size, pages=page.pages
    )


async def set_checkin(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    data: CheckinUpdate,
) -> CheckinRead:
    """Set or replace a profile's check-in and commit.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        profile_id: Profile to set the entry for.
        data: Accommodation and room.

    Returns:
        The stored entry.

    Raises:
        CheckinProfileNotFoundError: The profile is not on this trip.
        CheckinForbiddenError: The caller may not change this profile.
        CheckinTripOverError: The trip has ended.
    """
    profile = await _editable_profile(session, membership, profile_id)
    if await _purge_if_over(session, membership):
        raise CheckinTripOverError(str(membership.trip_id))
    row = await db.upsert_checkin(session, membership.trip_id, profile_id, data)
    await session.commit()
    return _read(row, profile, membership.sub)


async def clear_checkin(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> None:
    """Remove a profile's check-in and commit (nothing to remove is fine).

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        profile_id: Profile whose entry goes.

    Raises:
        CheckinProfileNotFoundError: The profile is not on this trip.
        CheckinForbiddenError: The caller may not change this profile.
    """
    await _editable_profile(session, membership, profile_id)
    await db.delete_checkin(session, membership.trip_id, profile_id)
    await session.commit()
