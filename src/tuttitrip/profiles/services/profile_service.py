"""Create, edit, delete profiles and set weights."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles import db
from tuttitrip.profiles.logic.age_defaults import (
    DEFAULTS,
    ComfortDefaults,
    age_group_for,
    customized_fields,
)
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
    comfort_problem,
)
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

NULLABLE_FIELDS = frozenset({"nap_start", "user_sub"})
UNIQUE_ACCOUNT = "uq_profiles_trip_id"
# The token has no name or age, so the host's own profile starts as a generic
# adult that they edit themselves.
HOST_NAME = "Organizator"
HOST_AGE = 35


class ProfileNotFoundError(Exception):
    """The profile is not on this trip."""


class ProfileForbiddenError(Exception):
    """The caller may not change this profile."""


class ProfileComfortError(ValueError):
    """The comfort fields contradict each other."""


class ProfileAccountError(Exception):
    """The account link cannot change: it is not allowed, or already taken."""


MEMBERSHIP_VIA_MEMBERS = (
    "This profile belongs to a trip member; change membership with "
    "DELETE /trips/{trip_id}/members/{profile_id}"
)


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
    if await db.select_account_profile_id(session, trip_id, sub) is not None:
        msg = "The account already has a profile on this trip"
        raise ProfileAccountError(msg)


@asynccontextmanager
async def _account_conflicts(session: AsyncSession) -> AsyncGenerator[None]:
    """Map a racing duplicate account link (the unique constraint) to a domain error.

    Args:
        session: Open session, rolled back on that violation.

    Yields:
        Nothing; wrap the flush and commit.

    Raises:
        ProfileAccountError: When the account got a profile in the meantime.
    """
    try:
        yield
    except IntegrityError as exc:
        await session.rollback()
        if UNIQUE_ACCOUNT not in str(exc.orig):
            raise
        msg = "The account already has a profile on this trip"
        raise ProfileAccountError(msg) from exc


def _consistent_nap(given: dict[str, Any]) -> dict[str, Any]:
    """Make an explicit "no nap" on one nap field apply to the other too.

    Args:
        given: Comfort fields the caller set.

    Returns:
        ``given`` with ``nap_minutes`` 0 after ``nap_start: null`` and the other
        way round, so a lone "no nap" overrides the age default completely.
    """
    out = dict(given)
    if "nap_start" in out and out["nap_start"] is None:
        out.setdefault("nap_minutes", 0)
    if out.get("nap_minutes") == 0:
        out.setdefault("nap_start", None)
    return out


def _check_comfort(values: dict[str, Any]) -> None:
    """Raise unless the comfort fields agree with each other.

    Args:
        values: Final comfort fields of a person.

    Raises:
        ProfileComfortError: On a contradiction.
    """
    problem = comfort_problem(
        values["segment_km"],
        values["daily_km"],
        values["nap_start"],
        values["nap_minutes"],
    )
    if problem is not None:
        raise ProfileComfortError(problem)


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
    return [_read(profile) for profile in profiles]


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
    comfort |= _consistent_nap(
        {
            k: v
            for k, v in given.items()
            if k in comfort and (v is not None or k == "nap_start")
        }
    )
    _check_comfort(comfort)
    profile = Profile(
        trip_id=membership.trip_id,
        display_name=data.display_name,
        age=data.age,
        user_sub=data.user_sub,
        **comfort,
    )
    async with _account_conflicts(session):
        await db.insert_profile(session, profile)
        await session.commit()
    return _read(profile)


async def create_host_profile(session: AsyncSession, trip_id: UUID, sub: str) -> None:
    """Give a new trip's host their own profile (adult defaults); caller commits.

    Args:
        session: Open session.
        trip_id: The just created trip.
        sub: Auth0 subject of the host.
    """
    await create_account_profile(session, trip_id, sub, HOST_NAME)


async def create_account_profile(
    session: AsyncSession, trip_id: UUID, sub: str, display_name: str
) -> UUID:
    """Create an adult profile (age defaults) linked to an account; caller commits.

    Used for the host of a new trip and for people who join by invitation.

    Args:
        session: Open session.
        trip_id: The trip.
        sub: Auth0 subject of the person.
        display_name: Name on the profile.

    Returns:
        The new profile's id.
    """
    profile = await db.insert_profile(
        session,
        Profile(
            trip_id=trip_id,
            display_name=display_name,
            age=HOST_AGE,
            user_sub=sub,
            **asdict(DEFAULTS[age_group_for(HOST_AGE)]),
        ),
    )
    return profile.id


async def find_account_profile(
    session: AsyncSession, trip_id: UUID, sub: str
) -> UUID | None:
    """The id of the profile linked to an account on a trip.

    Args:
        session: Open session.
        trip_id: The trip.
        sub: Auth0 subject.

    Returns:
        The profile id, or None when the account has no profile there.
    """
    return await db.select_account_profile_id(session, trip_id, sub)


async def _get(session: AsyncSession, trip_id: UUID, profile_id: UUID) -> Profile:
    profile = await db.select_profile(session, trip_id, profile_id)
    if profile is None:
        raise ProfileNotFoundError(str(profile_id))
    return profile


def _comfort(profile: Profile) -> ComfortDefaults:
    return ComfortDefaults(
        **{k: getattr(profile, k) for k in asdict(DEFAULTS[AgeGroup.ADULT])}
    )


def _read(profile: Profile) -> ProfileRead:
    """Profile DTO with ``customized_fields`` computed from the age defaults.

    Args:
        profile: The stored profile.

    Returns:
        The DTO.
    """
    read = ProfileRead.model_validate(profile)
    read.customized_fields = customized_fields(
        _comfort(profile), age_group_for(profile.age)
    )
    return read


def _follow_new_group(profile: Profile, new_age: int) -> dict[str, Any]:
    """Defaults of the new age group for the fields still at the old defaults.

    Args:
        profile: The person before the change.
        new_age: Their new age.

    Returns:
        Fields to reset; empty within one group, and fields the host corrected
        by hand are left out.
    """
    old, new = age_group_for(profile.age), age_group_for(new_age)
    if old is new:
        return {}
    kept = customized_fields(_comfort(profile), old)
    return {k: v for k, v in asdict(DEFAULTS[new]).items() if k not in kept}


async def get_profile(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> ProfileRead:
    """Read one profile of the trip.

    Args:
        session: Open session.
        membership: The caller's checked membership of the trip.
        profile_id: Profile to read.

    Returns:
        The profile.
    """
    return _read(await _get(session, membership.trip_id, profile_id))


async def unlink_account(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> None:
    """Detach the account from a profile; the person stays as a profile without one.

    Used when a member leaves the trip: the profile keeps counting in the plan.
    Flushes, the caller commits.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        profile_id: Profile to detach.
    """
    profile = await _get(session, membership.trip_id, profile_id)
    profile.user_sub = None
    await session.flush()


async def update_profile(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    data: ProfileUpdate,
) -> ProfileRead:
    """Edit a profile: your own, or any when you are a co-host or above.

    When the age moves the person to another age group, comfort fields still
    at the old group's defaults (and not in the payload) follow the new
    group's defaults; fields the host corrected stay.

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
        ProfileComfortError: The result would contradict itself.
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
        if profile.user_sub is not None and changes["user_sub"] != profile.user_sub:
            raise ProfileAccountError(MEMBERSHIP_VIA_MEMBERS)
        if changes["user_sub"] is not None and changes["user_sub"] != profile.user_sub:
            await _check_account(session, membership.trip_id, changes["user_sub"])
    changes = _consistent_nap(changes)
    if "age" in changes:
        changes = _follow_new_group(profile, changes["age"]) | changes
    current = asdict(_comfort(profile))
    _check_comfort(current | changes)
    for key, value in changes.items():
        setattr(profile, key, value)
    async with _account_conflicts(session):
        await session.commit()
    return _read(profile)


async def delete_profile(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> None:
    """Remove a person from the trip.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        profile_id: Profile to delete.

    Raises:
        ProfileAccountError: The profile has an account (a trip member).
    """
    profile = await _get(session, membership.trip_id, profile_id)
    if profile.user_sub is not None:
        raise ProfileAccountError(MEMBERSHIP_VIA_MEMBERS)
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
        people = [WeightSubject(p.id, p.age_group) for p in profiles.values()]
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
    return [_read(p) for p in profiles.values()]
