"""Invitation queries on PostgreSQL."""

from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import ColumnElement, Update, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.trips.invitations.models import TripInvitation
from tuttitrip.trips.models import Trip


class Found(NamedTuple):
    """An invitation with the name and destination of its trip."""

    invitation: TripInvitation
    trip_name: str
    destination: str | None


def _active(now: datetime) -> tuple[ColumnElement[bool], ...]:
    return (TripInvitation.revoked_at.is_(None), TripInvitation.expires_at > now)


async def insert_invitation(
    session: AsyncSession, row: TripInvitation
) -> TripInvitation:
    """Insert an invitation and flush to get server defaults.

    Args:
        session: Open session (caller commits).
        row: The new invitation.

    Returns:
        The same row, refreshed.
    """
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def select_by_hash(
    session: AsyncSession, token_hash: str, now: datetime
) -> Found | None:
    """The invitation with this token hash, if it has not expired or been revoked.

    Args:
        session: Open session.
        token_hash: SHA-256 of the presented token (the lookup is the comparison).
        now: Current time.

    Returns:
        The invitation (possibly used up) with its trip's name, or None.
    """
    result = await session.execute(
        select(TripInvitation, Trip.name, Trip.destination)
        .join(Trip, Trip.id == TripInvitation.trip_id)
        .where(TripInvitation.token_hash == token_hash, *_active(now))
    )
    found = result.tuples().first()
    return None if found is None else Found(*found)


async def select_for_trip(
    session: AsyncSession, trip_id: UUID
) -> Sequence[TripInvitation]:
    """Invitations of a trip, newest first.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        The invitations.
    """
    result = await session.scalars(
        select(TripInvitation)
        .where(TripInvitation.trip_id == trip_id)
        .order_by(TripInvitation.created_at.desc())
    )
    return result.all()


async def select_one(
    session: AsyncSession, trip_id: UUID, invitation_id: UUID
) -> TripInvitation | None:
    """One invitation of a trip.

    Args:
        session: Open session.
        trip_id: Trip the caller was checked for.
        invitation_id: Invitation id.

    Returns:
        The invitation, or None.
    """
    return await session.scalar(
        select(TripInvitation).where(
            TripInvitation.trip_id == trip_id, TripInvitation.id == invitation_id
        )
    )


async def count_usable(session: AsyncSession, trip_id: UUID, now: datetime) -> int:
    """How many invitations of the trip still work.

    Args:
        session: Open session.
        trip_id: Trip id.
        now: Current time.

    Returns:
        Number of unexpired, unrevoked invitations with uses left.
    """
    count = await session.scalar(
        select(func.count()).where(
            TripInvitation.trip_id == trip_id,
            TripInvitation.uses < TripInvitation.max_uses,
            *_active(now),
        )
    )
    return count or 0


def consume_stmt(invitation_id: UUID, now: datetime) -> Update:
    """The single conditional UPDATE that takes one use.

    Args:
        invitation_id: Invitation id.
        now: Current time.

    Returns:
        The statement; it returns the id only if a use was free.
    """
    return (
        update(TripInvitation)
        .where(
            TripInvitation.id == invitation_id,
            TripInvitation.uses < TripInvitation.max_uses,
            *_active(now),
        )
        .values(uses=TripInvitation.uses + 1)
        .returning(TripInvitation.id)
    )


async def consume_use(
    session: AsyncSession, invitation_id: UUID, now: datetime
) -> bool:
    """Take one use (safe under concurrent accepts).

    Args:
        session: Open session (caller commits).
        invitation_id: Invitation id.
        now: Current time.

    Returns:
        False when the invitation was revoked, expired or used up in the meantime.
    """
    result = await session.execute(consume_stmt(invitation_id, now))
    return result.scalar_one_or_none() is not None
