"""Read and replace the preferences of people on a trip."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.places.services import place_service
from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingUpdate,
    RatingValue,
    ReasonCode,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.preferences import db
from tuttitrip.profiles.preferences.logic.access import (
    effective_stairs_sensitivity,
)
from tuttitrip.profiles.preferences.logic.importance import default_pool
from tuttitrip.profiles.preferences.models import ProfilePreferences
from tuttitrip.profiles.preferences.schemas import (
    Constraints,
    ExamplePlace,
    ExampleVerdict,
    ImportancePool,
    PreferencesRead,
    PreferencesWrite,
)
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import (
    ProfileForbiddenError,
    ProfileNotFoundError,
)
from tuttitrip.trips.schemas import TripMembership, TripRole

__all__ = [
    "ExamplePlaceNotFoundError",
    "ProfileForbiddenError",
    "ProfileNotFoundError",
    "get_preferences",
    "list_preferences",
    "replace_preferences",
]

_VERDICT = {
    RatingValue.WANT: ExampleVerdict.LIKE,
    RatingValue.DONT_WANT: ExampleVerdict.DISLIKE,
}


class ExamplePlaceNotFoundError(Exception):
    """An example place names a ``place_id`` that is not in the catalog."""

    def __init__(self, place_id: UUID) -> None:
        """Remember the missing place.

        Args:
            place_id: The id that is not in the catalog.
        """
        super().__init__(f"Example place {place_id} is not in the catalog")


def _staff(membership: TripMembership) -> bool:
    return membership.role.satisfies(TripRole.CO_HOST)


def _default_pool(profile: ProfileRead) -> ImportancePool:
    return ImportancePool.model_validate(
        {d.value: p for d, p in default_pool(profile.age_group).items()}
    )


def _catalog_examples(
    ratings: list[RatingRead], places: dict[UUID, PlaceRead]
) -> list[ExamplePlace]:
    """Thumb ratings of one person as example places (want is like).

    Returns:
        One entry per want or dont_want rating whose place is known.
    """
    return [
        ExamplePlace(
            name=places[r.place_id].name, place_id=r.place_id, verdict=_VERDICT[r.value]
        )
        for r in ratings
        if r.value in _VERDICT and r.place_id in places
    ]


def _read(
    profile: ProfileRead,
    row: ProfilePreferences | None,
    catalog: list[ExamplePlace],
    *,
    sees_health: bool,
) -> PreferencesRead:
    """Preferences of a person; the age defaults while nothing is saved.

    Args:
        profile: The person.
        row: Their stored preferences, if any (nothing is persisted for none).
        catalog: Their rated catalog places, as examples.
        sees_health: Whether the viewer may see constraints.

    Returns:
        The DTO, with ``filled`` false for the defaults.
    """
    data = {
        "interests": row.interests if row else {},
        "diet": row.diet if row else {},
        "min_tags": row.min_tags if row else [],
        "example_places": [*(row.example_places if row else []), *catalog],
    }
    stored = Constraints.model_validate(row.constraints if row else {})
    return PreferencesRead.model_validate(
        {
            **data,
            "profile_id": profile.id,
            "importance_pool": (row.importance_pool if row else _default_pool(profile)),
            "constraints": stored if sees_health else None,
            "effective_stairs_sensitivity": (
                effective_stairs_sensitivity(stored, profile.stairs_sensitivity)
                if sees_health
                else None
            ),
            "filled": row is not None,
            "updated_by_sub": row.updated_by_sub if row else None,
            "updated_at": row.updated_at if row else None,
        }
    )


async def _read_many(
    session: AsyncSession,
    membership: TripMembership,
    profiles: list[ProfileRead],
) -> list[PreferencesRead]:
    rows = {
        row.profile_id: row
        for row in await db.select_preferences_by_trip(session, membership.trip_id)
    }
    ratings = await feedback_service.list_ratings(session, membership)
    mine = {p.id for p in profiles}
    wanted = [r for r in ratings if r.profile_id in mine and r.value in _VERDICT]
    places = await place_service.get_places(session, {r.place_id for r in wanted})
    return [
        _read(
            profile,
            rows.get(profile.id),
            _catalog_examples(
                [r for r in wanted if r.profile_id == profile.id], places
            ),
            sees_health=_staff(membership) or profile.user_sub == membership.sub,
        )
        for profile in profiles
    ]


def assumed_preferences(profiles: list[ProfileRead]) -> list[PreferencesRead]:
    """Age-default preferences for people that exist only in a draft plan.

    Args:
        profiles: The assumed people (see ``profile_service.assumed_adults``).

    Returns:
        One entry per person, ``filled`` false, nothing stored.
    """
    return [_read(profile, None, [], sees_health=True) for profile in profiles]


async def get_preferences(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> PreferencesRead:
    """Preferences of one person on the caller's trip.

    Args:
        session: Open session.
        membership: The caller's checked membership.
        profile_id: Whose preferences.

    Returns:
        The stored preferences, or the age defaults; constraints only for the
        person and for co-hosts and above.
    """
    profile = await profile_service.get_profile(session, membership, profile_id)
    return (await _read_many(session, membership, [profile]))[0]


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
    profiles = await profile_service.list_profiles(session, membership)
    return await _read_many(session, membership, profiles)


async def replace_preferences(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    data: PreferencesWrite,
) -> PreferencesRead:
    """Replace one person's preferences: their own, or any as a co-host or above.

    Example places with a ``place_id`` become the person's thumb ratings in the
    same transaction; the others are stored as text. The profile itself is not
    touched (the stairs constraint is applied on read).

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
    profile = await profile_service.get_profile(session, membership, profile_id)
    if not _staff(membership) and profile.user_sub != membership.sub:
        msg = "You can only edit your own preferences"
        raise ProfileForbiddenError(msg)
    catalog = {e.place_id: e for e in data.example_places if e.place_id is not None}
    known = await place_service.get_places(session, catalog.keys())
    for place_id in catalog:
        if place_id not in known:
            raise ExamplePlaceNotFoundError(place_id)
    pool = data.importance_pool or _default_pool(profile)
    dumped = data.model_dump(mode="json", exclude={"importance_pool"})
    dumped["importance_pool"] = pool.model_dump()
    dumped["example_places"] = [
        e.model_dump(mode="json") for e in data.example_places if e.place_id is None
    ]
    await db.upsert_preferences(session, profile_id, dumped, membership.sub)
    for place_id, example in catalog.items():
        liked = example.verdict is ExampleVerdict.LIKE
        rating = RatingUpdate(
            value=RatingValue.WANT if liked else RatingValue.DONT_WANT,
            reason_code=None if liked else ReasonCode.OTHER,
        )
        await feedback_service.stage_rating(
            session, membership, profile_id, place_id, rating
        )
    await session.commit()
    return (await _read_many(session, membership, [profile]))[0]
