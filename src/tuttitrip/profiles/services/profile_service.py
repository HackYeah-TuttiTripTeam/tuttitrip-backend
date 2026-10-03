"""Create, edit, delete profiles and set weights."""

from collections.abc import Awaitable
from dataclasses import asdict
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles import db
from tuttitrip.profiles.logic.age_defaults import DEFAULTS, age_group_for
from tuttitrip.profiles.logic.weight_presets import (
    WeightSubject,
    preset_weights,
    validate_weights,
)
from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.schemas import (
    AgeGroup,
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    WeightsUpdate,
)
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

NULLABLE_FIELDS = frozenset({"nap_start", "user_sub"})


class ProfileNotFoundError(Exception):
    """The profile is not on this trip."""


class ProfileForbiddenError(Exception):
    """The caller may not change this profile."""


class ProfileAccountError(Exception):
    """The account cannot be linked: not on the trip or already has a profile."""


async def _check_account(session: AsyncSession, trip_id: UUID, sub: str) -> None:
    """Raise unless ``sub`` is on the trip and has no profile there yet.

    Raises:
        ProfileAccountError: When linking is not possible.
    """
    try:
        await trip_service.get_membership(session, trip_id, sub, TripRole.MEMBER)
    except trip_service.TripNotFoundError as exc:
        msg = "The account is not a member of this trip"
        raise ProfileAccountError(msg) from exc
    if await db.user_has_profile(session, trip_id, sub):
        msg = "The account already has a profile on this trip"
        raise ProfileAccountError(msg)


async def _commit(
    session: AsyncSession, pending: Awaitable[object] | None = None
) -> None:
    """Run ``pending`` (a flush) and commit; map a racing duplicate account link.

    Args:
        session: Open session.
        pending: An awaitable that flushes, if the caller has one.

    Raises:
        ProfileAccountError: When the account got a profile in the meantime.
    """
    try:
        if pending is not None:
            await pending
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        msg = "The account already has a profile on this trip"
        raise ProfileAccountError(msg) from exc


async def list_profiles(
    session: AsyncSession, membership: TripMembership
) -> list[ProfileRead]:
    """List profiles of a trip.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.

    Returns:
        The trip's profiles.
    """
    profiles = await db.select_profiles_by_trip(session, membership.trip_id)
    return [ProfileRead.model_validate(profile) for profile in profiles]


async def create_profile(
    session: AsyncSession, membership: TripMembership, data: ProfileCreate
) -> ProfileRead:
    """Add a person; unset comfort fields come from the age group defaults.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        data: The payload.

    Returns:
        The created profile.
    """
    if data.user_sub is not None:
        await _check_account(session, membership.trip_id, data.user_sub)
    group = age_group_for(data.age)
    comfort = asdict(DEFAULTS[group])
    given = data.model_dump(exclude_unset=True)
    comfort |= {
        k: v
        for k, v in given.items()
        if k in comfort and (v is not None or k == "nap_start")
    }
    profile = Profile(
        trip_id=membership.trip_id,
        display_name=data.display_name,
        age=data.age,
        age_group=group.value,
        user_sub=data.user_sub,
        **comfort,
    )
    await _commit(session, db.insert_profile(session, profile))
    return ProfileRead.model_validate(profile)


async def _get(session: AsyncSession, trip_id: UUID, profile_id: UUID) -> Profile:
    profile = await db.select_profile(session, trip_id, profile_id)
    if profile is None:
        raise ProfileNotFoundError(str(profile_id))
    return profile


async def update_profile(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    data: ProfileUpdate,
) -> ProfileRead:
    """Edit a profile: your own, or any when you are a co-host or above.

    When the age moves the person to another age group, comfort fields not
    in the payload follow the new group's defaults.

    Args:
        session: Open session.
        membership: The caller's checked membership.
        profile_id: Profile to edit.
        data: Fields to change.

    Returns:
        The updated profile.

    Raises:
        ProfileForbiddenError: Not your profile, or linking an account without
            co-host rights.
    """
    profile = await _get(session, membership.trip_id, profile_id)
    is_staff = membership.role.satisfies(TripRole.CO_HOST)
    if not is_staff and profile.user_sub != membership.sub:
        msg = "You can only edit your own profile"
        raise ProfileForbiddenError(msg)
    changes = {
        k: v
        for k, v in data.model_dump(exclude_unset=True).items()
        if v is not None or k in NULLABLE_FIELDS
    }
    if "user_sub" in changes:
        if not is_staff:
            msg = "Only a co-host can link an account"
            raise ProfileForbiddenError(msg)
        if changes["user_sub"] is not None and changes["user_sub"] != profile.user_sub:
            await _check_account(session, membership.trip_id, changes["user_sub"])
    if "age" in changes:
        group = age_group_for(changes["age"])
        if group.value != profile.age_group:
            changes = asdict(DEFAULTS[group]) | changes
        changes["age_group"] = group.value
    for key, value in changes.items():
        setattr(profile, key, value)
    await _commit(session)
    return ProfileRead.model_validate(profile)


async def delete_profile(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> None:
    """Remove a person from the trip.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        profile_id: Profile to delete.
    """
    profile = await _get(session, membership.trip_id, profile_id)
    await db.delete_profile(session, profile)
    await session.commit()


async def set_weights(
    session: AsyncSession, membership: TripMembership, data: WeightsUpdate
) -> list[ProfileRead]:
    """Set weights by a preset or by hand, keeping ``max/min <= 3``.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        data: A preset (with a chosen person for ``dzien_babci``) or a weight list.

    Returns:
        All profiles of the trip with the new weights.

    Raises:
        ProfileNotFoundError: A named profile is not on this trip.
    """
    profiles = {
        p.id: p for p in await db.select_profiles_by_trip(session, membership.trip_id)
    }
    if data.preset is not None:
        if data.focus_profile_id is not None and data.focus_profile_id not in profiles:
            raise ProfileNotFoundError(str(data.focus_profile_id))
        people = [WeightSubject(p.id, AgeGroup(p.age_group)) for p in profiles.values()]
        new = preset_weights(data.preset, people, data.focus_profile_id)
    else:
        new = {item.profile_id: item.weight for item in data.weights or []}
        unknown = new.keys() - profiles.keys()
        if unknown:
            raise ProfileNotFoundError(str(next(iter(unknown))))
    final = {pid: new.get(pid, p.weight) for pid, p in profiles.items()}
    validate_weights(list(final.values()))
    for pid, weight in new.items():
        profiles[pid].weight = weight
    await session.commit()
    return [ProfileRead.model_validate(p) for p in profiles.values()]
