"""Read and replace the preferences of people on a trip."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places.services import place_service
from tuttitrip.profiles import db as profile_db
from tuttitrip.profiles.logic.age_defaults import DEFAULTS
from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.preferences import db
from tuttitrip.profiles.preferences.logic.access import stairs_sensitivity
from tuttitrip.profiles.preferences.logic.importance import default_pool
from tuttitrip.profiles.preferences.models import ProfilePreferences
from tuttitrip.profiles.preferences.schemas import (
    ImportancePool,
    PreferencesRead,
    PreferencesWrite,
)
from tuttitrip.profiles.services.profile_service import (
    ProfileForbiddenError,
    ProfileNotFoundError,
)
from tuttitrip.trips.schemas import TripMembership, TripRole


class ExamplePlaceNotFoundError(Exception):
    """An example place names a ``place_id`` that is not in the catalog."""


def _default_pool(profile: Profile) -> ImportancePool:
    return ImportancePool.model_validate(
        {d.value: p for d, p in default_pool(profile.age_group).items()}
    )


def _read(profile: Profile, row: ProfilePreferences | None) -> PreferencesRead:
    """Preferences of a person; the age defaults while nothing is saved.

    Args:
        profile: The person.
        row: Their stored preferences, if any.

    Returns:
        The DTO, with ``filled`` false for the defaults.
    """
    if row is None:
        pool = _default_pool(profile)
        return PreferencesRead(
            profile_id=profile.id,
            importance_pool=pool,
            filled=False,
            updated_by_sub=None,
            updated_at=None,
        )
    return PreferencesRead.model_validate(
        {
            "profile_id": profile.id,
            "interests": row.interests,
            "importance_pool": row.importance_pool,
            "constraints": row.constraints,
            "diet": row.diet,
            "example_places": row.example_places,
            "min_tags": row.min_tags,
            "filled": True,
            "updated_by_sub": row.updated_by_sub,
            "updated_at": row.updated_at,
        }
    )


async def _profile(session: AsyncSession, trip_id: UUID, profile_id: UUID) -> Profile:
    profile = await profile_db.select_profile(session, trip_id, profile_id)
    if profile is None:
        raise ProfileNotFoundError(str(profile_id))
    return profile


async def get_preferences(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> PreferencesRead:
    """Preferences of one person on the caller's trip.

    Args:
        session: Open session.
        membership: The caller's checked membership.
        profile_id: Whose preferences.

    Returns:
        The stored preferences, or the age defaults.
    """
    profile = await _profile(session, membership.trip_id, profile_id)
    return _read(profile, await db.select_preferences(session, profile_id))


async def list_preferences(
    session: AsyncSession, membership: TripMembership
) -> list[PreferencesRead]:
    """Preferences of everyone on the trip ("what we already know").

    Args:
        session: Open session.
        membership: The caller's checked membership.

    Returns:
        One entry per profile, defaults for those without saved preferences.
    """
    profiles = await profile_db.select_profiles_by_trip(session, membership.trip_id)
    rows = {
        row.profile_id: row
        for row in await db.select_preferences_by_trip(session, membership.trip_id)
    }
    return [_read(profile, rows.get(profile.id)) for profile in profiles]


async def replace_preferences(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    data: PreferencesWrite,
) -> PreferencesRead:
    """Replace one person's preferences: their own, or any as a co-host or above.

    Stairs or wheelchair also set the profile's ``stairs_sensitivity``.

    Args:
        session: Open session.
        membership: The caller's checked membership.
        profile_id: Whose preferences.
        data: The new preferences; an omitted pool is the age default.

    Returns:
        The saved preferences.

    Raises:
        ProfileForbiddenError: Not your profile and you are below co-host.
        ExamplePlaceNotFoundError: An example ``place_id`` is not in the catalog.
    """
    profile = await _profile(session, membership.trip_id, profile_id)
    if (
        not membership.role.satisfies(TripRole.CO_HOST)
        and profile.user_sub != membership.sub
    ):
        msg = "You can only edit your own preferences"
        raise ProfileForbiddenError(msg)
    for example in data.example_places:
        if example.place_id is not None:
            try:
                await place_service.get_place(session, example.place_id)
            except place_service.PlaceNotFoundError as exc:
                raise ExamplePlaceNotFoundError(str(example.place_id)) from exc
    pool = data.importance_pool or _default_pool(profile)
    dumped = data.model_dump(mode="json", exclude={"importance_pool"})
    dumped["importance_pool"] = pool.model_dump()
    row = await db.select_preferences(session, profile_id)
    if row is None:
        row = await db.insert_preferences(
            session,
            ProfilePreferences(
                profile_id=profile_id, updated_by_sub=membership.sub, **dumped
            ),
        )
    else:
        for key, value in dumped.items():
            setattr(row, key, value)
        row.updated_by_sub = membership.sub
    profile.stairs_sensitivity = stairs_sensitivity(
        blocked=data.constraints.stairs or data.constraints.wheelchair,
        current=profile.stairs_sensitivity,
        age_default=DEFAULTS[profile.age_group].stairs_sensitivity,
    )
    await session.commit()
    await session.refresh(row)
    return _read(profile, row)
