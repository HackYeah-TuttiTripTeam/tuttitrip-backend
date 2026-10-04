"""Rate places and file vetoes.

The author comes in as a parameter (a trip membership), never as the logged-in
user, so a voting link without an account can reuse the same service.
"""

from collections.abc import Collection
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.notifications.schemas import (
    NotificationAction,
    NotificationActionCode,
    NotificationType,
)
from tuttitrip.notifications.services import notification_service
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.places.services import place_service
from tuttitrip.places.services.place_service import PlaceNotFoundError
from tuttitrip.profiles import db as profiles_db
from tuttitrip.profiles.feedback import db
from tuttitrip.profiles.feedback.models import PlaceRating, PlaceVeto
from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingUpdate,
    RatingValue,
    TripFeedback,
    VetoCreate,
    VetoRead,
    VotingActor,
)
from tuttitrip.profiles.models import Profile
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import member_service

ACTIVE_VETO_INDEX = "uq_place_vetoes_active"


class ProfileNotFoundError(Exception):
    """The profile is not on this trip."""


class FeedbackPlaceNotFoundError(Exception):
    """The place is not in the catalog."""


class VetoNotFoundError(Exception):
    """The veto is not on this trip."""


class FeedbackForbiddenError(Exception):
    """The caller may act only for their own profile (or as a co-host)."""


class VetoExistsError(Exception):
    """The person already has an active veto on this place."""


async def _profile_for_author(
    session: AsyncSession, membership: TripMembership, profile_id: UUID
) -> Profile:
    """Load the profile and check the author may act for it.

    Returns:
        The profile.

    Raises:
        ProfileNotFoundError: The profile is not on the trip.
        FeedbackForbiddenError: Not the caller's own profile and below co-host.
    """
    profile = await profiles_db.select_profile(session, membership.trip_id, profile_id)
    if profile is None:
        raise ProfileNotFoundError(str(profile_id))
    own = profile.user_sub is not None and profile.user_sub == membership.sub
    if not own and not membership.role.satisfies(TripRole.CO_HOST):
        msg = "You can act only for your own profile (co-host or host for others)"
        raise FeedbackForbiddenError(msg)
    return profile


async def _require_place(session: AsyncSession, place_id: UUID) -> PlaceRead:
    try:
        return await place_service.get_place(session, place_id)
    except PlaceNotFoundError as exc:
        raise FeedbackPlaceNotFoundError(str(place_id)) from exc


async def stage_rating(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    place_id: UUID,
    data: RatingUpdate,
) -> PlaceRating:
    """Upsert a rating without committing, for callers that own the transaction.

    The caller has checked that the author may act for the profile and that the
    place exists (``rate_place`` does both).

    Args:
        session: Open session (caller commits).
        membership: The author's membership of the trip.
        profile_id: Whose rating it is.
        place_id: Catalog place.
        data: Value and, for ``dont_want``, the reason.

    Returns:
        The stored row.
    """
    return await db.upsert_rating(
        session,
        PlaceRating(
            trip_id=membership.trip_id,
            profile_id=profile_id,
            place_id=place_id,
            value=data.value,
            reason_code=data.reason_code,
            updated_by_sub=membership.sub,
        ),
    )


async def rate_place(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    place_id: UUID,
    data: RatingUpdate,
) -> RatingRead:
    """Set a person's rating of a catalog place; repeating it keeps one row.

    Args:
        session: Open session.
        membership: The author's membership of the trip.
        profile_id: Whose rating it is.
        place_id: Catalog place.
        data: Value and, for ``dont_want``, the reason.

    Returns:
        The stored rating.
    """
    await _profile_for_author(session, membership, profile_id)
    await _require_place(session, place_id)
    row = await stage_rating(session, membership, profile_id, place_id, data)
    read = RatingRead.model_validate(row)
    await session.commit()
    return read


async def like_places(
    session: AsyncSession,
    membership: TripMembership,
    profile_id: UUID,
    place_ids: Collection[UUID],
) -> frozenset[UUID]:
    """Mark places as wanted by a person, keeping the votes they already cast.

    One transaction for the whole batch (an import of a saved list). The places
    must exist: the caller took them from the catalog.

    Args:
        session: Open session.
        membership: The author's membership of the trip.
        profile_id: Whose votes they are.
        place_ids: Catalog places to mark as wanted.

    Returns:
        The places newly marked as wanted; the rest the person had already
        rated (any value) and are left as they were.
    """
    await _profile_for_author(session, membership, profile_id)
    rated = {
        r.place_id
        for r in await db.select_ratings_by_trip(session, membership.trip_id)
        if r.profile_id == profile_id
    }
    liked: set[UUID] = set()
    for place_id in dict.fromkeys(place_ids):
        if place_id in rated:
            continue
        await stage_rating(
            session,
            membership,
            profile_id,
            place_id,
            RatingUpdate(value=RatingValue.WANT),
        )
        liked.add(place_id)
    await session.commit()
    return frozenset(liked)


async def list_ratings(
    session: AsyncSession, membership: TripMembership
) -> list[RatingRead]:
    """List every rating on the trip.

    Args:
        session: Open session.
        membership: The caller's membership of the trip.

    Returns:
        All ratings of the trip.
    """
    rows = await db.select_ratings_by_trip(session, membership.trip_id)
    return [RatingRead.model_validate(row) for row in rows]


async def _insert_veto(session: AsyncSession, veto: PlaceVeto) -> PlaceVeto:
    """Insert a veto, translating the unique active-veto index into a domain error.

    Args:
        session: Open session (caller commits).
        veto: The new veto.

    Returns:
        The stored veto.

    Raises:
        VetoExistsError: The person already has an active veto on the place.
    """
    try:
        stored = await db.insert_veto(session, veto)
    except IntegrityError as exc:
        await session.rollback()
        if ACTIVE_VETO_INDEX not in str(exc.orig):
            raise
        msg = "This person already has an active veto on the place"
        raise VetoExistsError(msg) from exc
    await session.refresh(stored)
    return stored


async def create_veto(
    session: AsyncSession, membership: TripMembership, data: VetoCreate
) -> VetoRead:
    """File a veto, possibly on behalf of someone else.

    Args:
        session: Open session.
        membership: The author's membership of the trip.
        data: Whose veto and which place.

    Returns:
        The veto with its author; ``on_behalf`` when the profile is not theirs.

    Raises:
        VetoExistsError: The person already has an active veto on the place.
    """
    profile = await _profile_for_author(session, membership, data.profile_id)
    place = await _require_place(session, data.place_id)
    veto = await _insert_veto(
        session,
        PlaceVeto(
            trip_id=membership.trip_id,
            profile_id=data.profile_id,
            place_id=data.place_id,
            created_by_sub=membership.sub,
            on_behalf=profile.user_sub != membership.sub,
        ),
    )
    read = VetoRead.model_validate(veto)
    await notification_service.notify(
        session,
        recipients=await member_service.organizer_subs(session, membership.trip_id),
        type=NotificationType.VETO_ADDED,
        trip_id=membership.trip_id,
        params={"place_name": place.name, "member_name": profile.display_name},
        actions=[NotificationAction(code=NotificationActionCode.OPEN_PLAN)],
        # The veto id: a veto revoked and filed again is a new event.
        dedupe_key=f"veto:{veto.id}",
        actor=membership.sub,
    )
    await session.commit()
    return read


async def list_vetoes(
    session: AsyncSession, membership: TripMembership
) -> list[VetoRead]:
    """List the vetoes in force on the trip.

    Args:
        session: Open session.
        membership: The caller's membership of the trip.

    Returns:
        Active vetoes (revoked ones no longer block).
    """
    rows = await db.select_active_vetoes_by_trip(session, membership.trip_id)
    return [VetoRead.model_validate(row) for row in rows]


async def revoke_veto(
    session: AsyncSession, membership: TripMembership, veto_id: UUID
) -> None:
    """Revoke a veto so it stops blocking the place; repeating it is harmless.

    Args:
        session: Open session.
        membership: The caller's membership of the trip.
        veto_id: Veto to revoke.

    Raises:
        VetoNotFoundError: The veto is not on this trip.
    """
    veto = await db.select_veto(session, membership.trip_id, veto_id)
    if veto is None:
        raise VetoNotFoundError(str(veto_id))
    await _profile_for_author(session, membership, veto.profile_id)
    if veto.revoked_at is None:
        veto.revoked_at = datetime.now(UTC)
        veto.revoked_by_sub = membership.sub
        await session.commit()


async def list_for_trip(session: AsyncSession, trip_id: UUID) -> TripFeedback:
    """Ratings and active vetoes for the planner (E0 and E1 inputs).

    The caller (planning) has already checked access to the trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Every rating and the vetoes not revoked.
    """
    ratings = await db.select_ratings_by_trip(session, trip_id)
    vetoes = await db.select_active_vetoes_by_trip(session, trip_id)
    return TripFeedback(
        ratings=[RatingRead.model_validate(r) for r in ratings],
        vetoes=[VetoRead.model_validate(v) for v in vetoes],
    )


# --- acting for a profile by a given author (voting link, no account) ----------
# The caller has checked that the author may act for the profile (a voting token
# bound to it), so these take the author as a plain value, not a membership.


async def rate_by_author(
    session: AsyncSession, actor: VotingActor, place_id: UUID, data: RatingUpdate
) -> RatingRead:
    """Set a person's rating on behalf of a checked author and commit.

    Args:
        session: Open session.
        actor: Trip, profile and the author marker to store.
        place_id: Catalog place.
        data: Value and, for ``dont_want``, the reason.

    Returns:
        The stored rating.
    """
    await _require_place(session, place_id)
    row = await db.upsert_rating(
        session,
        PlaceRating(
            trip_id=actor.trip_id,
            profile_id=actor.profile_id,
            place_id=place_id,
            value=data.value,
            reason_code=data.reason_code,
            updated_by_sub=actor.author,
        ),
    )
    read = RatingRead.model_validate(row)
    await session.commit()
    return read


async def veto_by_author(
    session: AsyncSession, actor: VotingActor, place_id: UUID
) -> VetoRead:
    """File a person's veto for a checked author; a repeated veto is the same one.

    Args:
        session: Open session.
        actor: Trip, profile and the author marker to store.
        place_id: Catalog place.

    Returns:
        The veto in force (the existing one when it was already filed).
    """
    place = await _require_place(session, place_id)
    try:
        veto = await _insert_veto(
            session,
            PlaceVeto(
                trip_id=actor.trip_id,
                profile_id=actor.profile_id,
                place_id=place_id,
                created_by_sub=actor.author,
                on_behalf=False,
            ),
        )
    except VetoExistsError:
        veto = next(
            v
            for v in await db.select_active_vetoes_of_profile(
                session, actor.trip_id, actor.profile_id
            )
            if v.place_id == place_id
        )
    else:  # a new veto: the organizers are told, a repeat is not news
        profile = await profiles_db.select_profile(
            session, actor.trip_id, actor.profile_id
        )
        await notification_service.notify(
            session,
            recipients=await member_service.organizer_subs(session, actor.trip_id),
            type=NotificationType.VETO_ADDED,
            trip_id=actor.trip_id,
            params={
                "place_name": place.name,
                "member_name": profile.display_name if profile else "",
            },
            actions=[NotificationAction(code=NotificationActionCode.OPEN_PLAN)],
            dedupe_key=f"veto:{veto.id}",
            actor=actor.author,
        )
    read = VetoRead.model_validate(veto)
    await session.commit()
    return read


async def revoke_veto_by_author(
    session: AsyncSession, actor: VotingActor, veto_id: UUID
) -> VetoRead:
    """Revoke a person's own veto for a checked author; repeating it is harmless.

    Args:
        session: Open session.
        actor: Trip, profile (whose veto it must be) and the revoker marker.
        veto_id: Veto to revoke.

    Returns:
        The veto with ``revoked_at``.

    Raises:
        VetoNotFoundError: No such veto of this person on the trip.
    """
    veto = await db.select_veto(session, actor.trip_id, veto_id)
    if veto is None or veto.profile_id != actor.profile_id:
        raise VetoNotFoundError(str(veto_id))
    if veto.revoked_at is None:
        veto.revoked_at = datetime.now(UTC)
        veto.revoked_by_sub = actor.author
        await session.commit()
        await session.refresh(veto)
    return VetoRead.model_validate(veto)


async def list_for_profile(
    session: AsyncSession, trip_id: UUID, profile_id: UUID
) -> TripFeedback:
    """One person's ratings and active vetoes (nobody else's).

    Args:
        session: Open session.
        trip_id: Trip id.
        profile_id: Whose answers.

    Returns:
        The person's ratings and vetoes in force.
    """
    ratings = await db.select_ratings_of_profile(session, trip_id, profile_id)
    vetoes = await db.select_active_vetoes_of_profile(session, trip_id, profile_id)
    return TripFeedback(
        ratings=[RatingRead.model_validate(r) for r in ratings],
        vetoes=[VetoRead.model_validate(v) for v in vetoes],
    )
