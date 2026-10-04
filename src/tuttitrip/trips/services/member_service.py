"""Trip members: list, change role, remove, confirm, leave and hand over the host role.

The rules are in ``member_rules``.

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
from tuttitrip.trips.photos.services import photo_service
from tuttitrip.trips.schemas import MemberRead, MemberStatus, TripMembership, TripRole


class MemberNotFoundError(Exception):
    """No member of this trip has that profile (also: a profile without account)."""


class MemberForbiddenError(Exception):
    """The role rules do not allow this operation."""


class HostMustTransferError(Exception):
    """The host tried to leave; the trip needs a host, so they hand the role over."""


def _read(
    profile: ProfileRead, role: TripRole, status: MemberStatus, caller_sub: str
) -> MemberRead:
    return MemberRead(
        profile_id=profile.id,
        display_name=profile.display_name,
        role=role,
        status=status,
        is_me=profile.user_sub == caller_sub,
    )


async def _target(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> tuple[ProfileRead, str, TripRole, MemberStatus]:
    """The profile, account, trip role and status of the member behind ``profile_id``.

    Returns:
        The profile, its Auth0 subject, the member's role and status.

    Raises:
        MemberNotFoundError: Unknown profile, or one without an account on the trip.
    """
    try:
        profile = await profile_service.get_profile(session, membership, profile_id)
    except ProfileNotFoundError as exc:
        raise MemberNotFoundError(str(profile_id)) from exc
    sub = profile.user_sub
    found = (
        None
        if sub is None
        else await db.select_membership(session, membership.trip_id, sub)
    )
    if sub is None or found is None:
        raise MemberNotFoundError(str(profile_id))
    return profile, sub, *found


async def member_left(
    session: AsyncSession, trip_id: UUID, profile_id: UUID, sub: str
) -> None:
    """Clear what only members may keep on a trip, when one stops being a member.

    The single place for this cleanup: the removal route calls it, and so must
    any "leave the trip" route. Flushes, the caller commits.

    Args:
        session: Open session.
        trip_id: The trip.
        profile_id: Profile of the person who left (it stays on the trip).
        sub: Auth0 subject of the account that left.
    """
    await checkin_service.clear_profile(session, trip_id, profile_id)
    await photo_service.author_left(session, trip_id, sub)


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
    found = await db.select_members(session, membership.trip_id)
    profiles = await profile_service.list_profiles(session, membership)
    members = [
        _read(p, *found[p.user_sub], membership.sub)
        for p in profiles
        if p.user_sub in found
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
    profile, sub, current, status = await _target(session, membership, profile_id)
    if not member_rules.can_set_role(membership.role, current, role):
        msg = f"A {membership.role} cannot change a {current} to {role}"
        raise MemberForbiddenError(msg)
    await db.update_member_role(session, membership.trip_id, sub, role)
    await session.commit()
    return _read(profile, role, status, membership.sub)


async def remove_member(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> None:
    """Remove a member from the trip and commit.

    The account loses access (the trip is a 404 for it). The profile stays on
    the trip without an account, so the person still counts in the plan. Their
    check-in (room number) is deleted and their photos stay, unattributed.

    Args:
        session: Open session.
        membership: The caller's checked (co-host or host) membership.
        profile_id: Profile of the member.

    Raises:
        MemberNotFoundError: No such member.
        MemberForbiddenError: The role rules forbid the removal.
    """
    _, sub, current, _ = await _target(session, membership, profile_id)
    if not member_rules.can_remove(membership.role, current):
        msg = f"A {membership.role} cannot remove a {current}"
        raise MemberForbiddenError(msg)
    await db.delete_member(session, membership.trip_id, sub)
    await profile_service.unlink_account(session, membership, profile_id)
    await member_left(session, membership.trip_id, profile_id, sub)
    await session.commit()


async def confirm(session: AsyncSession, membership: TripMembership) -> MemberRead:
    """Confirm the caller's own participation and commit (idempotent).

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).

    Returns:
        The caller as a member, now ``confirmed``.

    Raises:
        MemberNotFoundError: The caller has no profile on the trip.
    """
    profile_id = await profile_service.find_account_profile(
        session, membership.trip_id, membership.sub
    )
    if profile_id is None:
        raise MemberNotFoundError(membership.sub)
    profile = await profile_service.get_profile(session, membership, profile_id)
    await db.update_member_status(
        session, membership.trip_id, membership.sub, MemberStatus.CONFIRMED
    )
    await session.commit()
    return _read(profile, membership.role, MemberStatus.CONFIRMED, membership.sub)


async def leave(session: AsyncSession, membership: TripMembership) -> None:
    """Leave the trip and commit.

    The account loses access (the trip is a 404 for it). The caller's profile
    stays on the trip without an account, like a removed member's, so their
    expenses and balance remain and the profile can be claimed again.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).

    Raises:
        HostMustTransferError: The caller is the host.
    """
    if not member_rules.can_leave(membership.role):
        raise HostMustTransferError
    profile_id = await profile_service.find_account_profile(
        session, membership.trip_id, membership.sub
    )
    await db.delete_member(session, membership.trip_id, membership.sub)
    if profile_id is not None:
        await profile_service.unlink_account(session, membership, profile_id)
        await member_left(session, membership.trip_id, profile_id, membership.sub)
    await session.commit()


async def transfer_host(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> MemberRead:
    """Make a member the host, the caller becomes a co-host, and commit.

    Args:
        session: Open session.
        membership: The caller's checked (host) membership.
        profile_id: Profile of the new host.

    Returns:
        The new host.

    Raises:
        MemberNotFoundError: No such member.
        MemberForbiddenError: The target already is the host.
    """
    profile, sub, current, status = await _target(session, membership, profile_id)
    if not member_rules.can_transfer_host(membership.role, current):
        msg = f"A {membership.role} cannot hand the host role to a {current}"
        raise MemberForbiddenError(msg)
    await db.update_member_role(session, membership.trip_id, sub, TripRole.HOST)
    await db.update_member_role(
        session, membership.trip_id, membership.sub, TripRole.CO_HOST
    )
    await session.commit()
    return _read(profile, TripRole.HOST, status, membership.sub)


async def organizer_subs(session: AsyncSession, trip_id: UUID) -> list[str]:
    """Accounts of the host and the co-hosts: who gets organizer notifications.

    Args:
        session: Open session.
        trip_id: The trip.

    Returns:
        Auth0 subjects of everyone with at least the co-host role.
    """
    members = await db.select_members(session, trip_id)
    return [
        sub for sub, (role, _) in members.items() if role.satisfies(TripRole.CO_HOST)
    ]
