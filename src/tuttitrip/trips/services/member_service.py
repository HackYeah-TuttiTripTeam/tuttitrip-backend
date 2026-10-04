"""Trip members: list, change role and remove, under the rules of ``member_rules``.

A member is a trip role (``trip_members``, keyed by account) plus the profile
linked to that account; the API addresses members by profile id.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import ProfileNotFoundError
from tuttitrip.trips import db
from tuttitrip.trips.checkins.services import checkin_service
from tuttitrip.trips.logic import member_rules
from tuttitrip.trips.schemas import MemberRead, TripMembership, TripRole


class MemberNotFoundError(Exception):
    """No member of this trip has that profile (also: a profile without account)."""


class MemberForbiddenError(Exception):
    """The role rules do not allow this operation."""


def _read(profile: ProfileRead, role: TripRole, caller_sub: str) -> MemberRead:
    return MemberRead(
        profile_id=profile.id,
        display_name=profile.display_name,
        role=role,
        is_me=profile.user_sub == caller_sub,
    )


async def _target(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> tuple[ProfileRead, str, TripRole]:
    """The profile, account and trip role of the member behind ``profile_id``.

    Returns:
        The profile, its Auth0 subject and the member's role.

    Raises:
        MemberNotFoundError: Unknown profile, or one without an account on the trip.
    """
    try:
        profile = await profile_service.get_profile(session, membership, profile_id)
    except ProfileNotFoundError as exc:
        raise MemberNotFoundError(str(profile_id)) from exc
    sub = profile.user_sub
    role = (
        None
        if sub is None
        else await db.select_member_role(session, membership.trip_id, sub)
    )
    if sub is None or role is None:
        raise MemberNotFoundError(str(profile_id))
    return profile, sub, role


async def member_left(session: AsyncSession, trip_id: UUID, profile_id: UUID) -> None:
    """Clear what only members may keep on a trip, when one stops being a member.

    The single place for this cleanup: the removal route calls it, and so must
    any "leave the trip" route. Flushes, the caller commits.

    Args:
        session: Open session.
        trip_id: The trip.
        profile_id: Profile of the person who left (it stays on the trip).
    """
    await checkin_service.clear_profile(session, trip_id, profile_id)


async def list_members(
    session: AsyncSession, membership: TripMembership
) -> list[MemberRead]:
    """List the people on the trip who have an account, highest role first.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).

    Returns:
        Members with the name from their profile; profiles without an account
        are not members.
    """
    roles = await db.select_member_roles(session, membership.trip_id)
    profiles = await profile_service.list_profiles(session, membership)
    members = [
        _read(p, roles[p.user_sub], membership.sub)
        for p in profiles
        if p.user_sub in roles
    ]
    return sorted(members, key=lambda m: (-m.role.rank, m.display_name))


async def set_role(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    role: TripRole,
) -> MemberRead:
    """Change a member's role and commit.

    Args:
        session: Open session.
        membership: The caller's checked (host) membership.
        profile_id: Profile of the member.
        role: The new role.

    Returns:
        The member after the change.

    Raises:
        MemberNotFoundError: No such member.
        MemberForbiddenError: The role rules forbid the change.
    """
    profile, sub, current = await _target(session, membership, profile_id)
    if not member_rules.can_set_role(membership.role, current, role):
        msg = f"A {membership.role} cannot change a {current} to {role}"
        raise MemberForbiddenError(msg)
    await db.update_member_role(session, membership.trip_id, sub, role)
    await session.commit()
    return _read(profile, role, membership.sub)


async def remove_member(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> None:
    """Remove a member from the trip and commit.

    The account loses access (the trip is a 404 for it). The profile stays on
    the trip without an account, so the person still counts in the plan. Their
    check-in (room number) is deleted.

    Args:
        session: Open session.
        membership: The caller's checked (co-host or host) membership.
        profile_id: Profile of the member.

    Raises:
        MemberNotFoundError: No such member.
        MemberForbiddenError: The role rules forbid the removal.
    """
    _, sub, current = await _target(session, membership, profile_id)
    if not member_rules.can_remove(membership.role, current):
        msg = f"A {membership.role} cannot remove a {current}"
        raise MemberForbiddenError(msg)
    await db.delete_member(session, membership.trip_id, sub)
    await profile_service.unlink_account(session, membership, profile_id)
    await member_left(session, membership.trip_id, profile_id)
    await session.commit()
