"""Trips: create, list, read, update, delete, and the object-level role check."""

from itertools import starmap
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.services import profile_service
from tuttitrip.trips import db
from tuttitrip.trips.models import Trip
from tuttitrip.trips.schemas import (
    TripCreate,
    TripDetails,
    TripMembership,
    TripRead,
    TripRole,
    TripUpdate,
    check_trip,
)


class TripNotFoundError(Exception):
    """The trip does not exist or the user is not on it (both look the same)."""


class TripRoleError(Exception):
    """The user is on the trip but their role is too low."""


class TripInvalidError(Exception):
    """The trip would break a rule after the change (carries pydantic errors)."""

    def __init__(self, errors: list[dict[str, object]]) -> None:
        """Keep the errors for the API to report.

        Args:
            errors: Pydantic error dicts (``loc``, ``msg``, ``type``).
        """
        super().__init__("Invalid trip details")
        self.errors = errors


def _read(trip: Trip, role: TripRole) -> TripRead:
    details = TripDetails.model_validate(trip, from_attributes=True)
    return TripRead(**details.model_dump(exclude={"kind"}), my_role=role)


async def create_trip(
    session: AsyncSession, owner_sub: str, data: TripCreate
) -> TripRead:
    """Create a trip with the caller as host (and their profile) and commit.

    Args:
        session: Open session.
        owner_sub: Auth0 subject of the organizer.
        data: Validated payload (already checked with ``check_trip``).

    Returns:
        The created trip.
    """
    trip = await db.insert_trip(
        session, owner_sub=owner_sub, fields=data.model_dump(exclude_unset=True)
    )
    await profile_service.create_host_profile(session, trip.id, owner_sub)
    await session.commit()
    return _read(trip, TripRole.HOST)


async def list_trips(session: AsyncSession, sub: str) -> list[TripRead]:
    """List the trips the user belongs to.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        Trips, newest first, with the user's role.
    """
    return list(starmap(_read, await db.select_trips_of_member(session, sub)))


async def get_membership(
    session: AsyncSession, trip_id: UUID, sub: str, min_role: TripRole
) -> TripMembership:
    """Check that the user has at least ``min_role`` on the trip.

    Other domains call this (or use ``TripAccess`` in their api.py) to
    authorize trip-scoped operations.

    Args:
        session: Open session.
        trip_id: Trip id.
        sub: Auth0 subject of the caller.
        min_role: Minimum trip role.

    Returns:
        The membership.
    """
    role = await db.select_member_role(session, trip_id, sub)
    if role is None:
        raise TripNotFoundError(str(trip_id))
    if not role.satisfies(min_role):
        msg = f"Trip role '{min_role}' required (you are '{role}')"
        raise TripRoleError(msg)
    return TripMembership(trip_id=trip_id, sub=sub, role=role)


async def get_trip(session: AsyncSession, membership: TripMembership) -> TripRead:
    """Read the trip the caller was checked for.

    Args:
        session: Open session.
        membership: Proof from ``TripAccess``.

    Returns:
        The trip with the caller's role.

    Raises:
        TripNotFoundError: The trip vanished after the access check.
    """
    trip = await db.select_trip(session, membership.trip_id)
    if trip is None:
        raise TripNotFoundError(str(membership.trip_id)) from None
    return _read(trip, membership.role)


async def update_trip(
    session: AsyncSession, membership: TripMembership, data: TripUpdate
) -> TripRead:
    """Apply a partial update and commit.

    The merged state is validated again, so a single changed field cannot
    break a range against a stored one.

    Args:
        session: Open session.
        membership: Proof from ``TripAccess`` (co-host or host).
        data: The fields to change.

    Returns:
        The updated trip.

    Raises:
        TripNotFoundError: The trip vanished after the access check.
        TripInvalidError: The merged trip breaks a range rule.
    """
    trip = await db.select_trip(session, membership.trip_id)
    if trip is None:
        raise TripNotFoundError(str(membership.trip_id))
    changes = data.model_dump(exclude_unset=True)
    merged = TripUpdate.model_validate(trip, from_attributes=True).model_copy(
        update=changes
    )
    try:
        check_trip(merged, complete=True)
    except ValidationError as exc:
        errors = exc.errors(
            include_url=False, include_input=False, include_context=False
        )
        raise TripInvalidError(
            [{**e, "loc": ("body", *e["loc"])} for e in errors]
        ) from exc
    for field, value in changes.items():
        setattr(trip, field, value)
    await session.commit()
    await session.refresh(trip)
    return _read(trip, membership.role)


async def delete_trip(session: AsyncSession, membership: TripMembership) -> None:
    """Delete the trip with everything under it and commit.

    Args:
        session: Open session.
        membership: Proof from ``TripAccess`` (host).
    """
    await db.delete_trip(session, membership.trip_id)
    await session.commit()
