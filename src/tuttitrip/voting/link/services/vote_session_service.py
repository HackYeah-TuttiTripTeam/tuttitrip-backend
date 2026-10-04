"""Voting through a link: the person's session, ratings and vetoes.

The token proves the trip and the profile (``TokenAccess``). The link works only
while the profile has no account: once somebody takes it over, the person logs
in to vote and the link is dead (``VoteLinkDeadError``, answered as 404 like any
bad token). Every write is stored with the author ``link:<token id>``, and a
veto recomputes the plan in the same request, because a person without an
account cannot ask for it themselves.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.places.services import place_service
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import PlanInputError
from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingUpdate,
    TripFeedback,
    VetoRead,
    VotingActor,
    link_author,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.profiles.services.profile_service import ProfileNotFoundError
from tuttitrip.shared.permissions.schemas import TokenAccess
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.voting.link.schemas import VotePlace, VoteSession


class VoteLinkDeadError(Exception):
    """The token's profile is gone or has an account now: the link no longer works."""


async def _check_link(
    session: AsyncSession, access: TokenAccess
) -> tuple[TripMembership, str]:
    """Return a member-level membership for the link and the person's name.

    Raises:
        VoteLinkDeadError: The profile does not exist or belongs to an account.
    """
    membership = TripMembership(
        trip_id=access.trip_id, sub=link_author(access.token_id), role=TripRole.MEMBER
    )
    try:
        profile = await profile_service.get_profile(
            session, membership, access.profile_id
        )
    except ProfileNotFoundError as exc:
        raise VoteLinkDeadError(str(access.token_id)) from exc
    if profile.user_sub is not None:
        raise VoteLinkDeadError(str(access.token_id))
    return membership, profile.display_name


def _actor(access: TokenAccess) -> VotingActor:
    return VotingActor(
        trip_id=access.trip_id,
        profile_id=access.profile_id,
        author=link_author(access.token_id),
    )


def _vote_place(
    place: PlaceRead, feedback: TripFeedback, in_plan: set[UUID]
) -> VotePlace:
    rating = next((r for r in feedback.ratings if r.place_id == place.id), None)
    veto_row = next((v for v in feedback.vetoes if v.place_id == place.id), None)
    return VotePlace(
        place_id=place.id,
        name=place.name,
        description=None,
        photo_url=None,
        category=place.category,
        address=place.address,
        in_plan=place.id in in_plan,
        rating=rating.value if rating else None,
        reason_code=rating.reason_code if rating else None,
        veto_id=veto_row.id if veto_row else None,
    )


async def _place(
    session: AsyncSession, access: TokenAccess, place_id: UUID
) -> VotePlace:
    feedback = await feedback_service.list_for_profile(
        session, access.trip_id, access.profile_id
    )
    in_plan = set(await plan_service.latest_place_ids(session, access.trip_id))
    place = await place_service.get_place(session, place_id)
    return _vote_place(place, feedback, in_plan)


async def read_session(session: AsyncSession, access: TokenAccess) -> VoteSession:
    """The places of the current plan with this person's answers, and nothing else.

    Args:
        session: Open session.
        access: The checked token.

    Returns:
        The trip name, the person's name and the places.

    Raises:
        VoteLinkDeadError: The link no longer works.
    """
    membership, name = await _check_link(session, access)
    trip = await trip_service.get_trip(session, membership)
    feedback = await feedback_service.list_for_profile(
        session, access.trip_id, access.profile_id
    )
    planned = await plan_service.latest_place_ids(session, access.trip_id)
    answered = {r.place_id for r in feedback.ratings} | {
        v.place_id for v in feedback.vetoes
    }
    places = await place_service.get_places(session, {*planned, *answered})
    in_plan = set(planned)
    extra = sorted(
        answered - in_plan,
        key=lambda i: (places[i].name, i) if i in places else ("", i),
    )
    ordered = [i for i in (*planned, *extra) if i in places]
    return VoteSession(
        trip_name=trip.name,
        profile_name=name,
        places=[_vote_place(places[i], feedback, in_plan) for i in ordered],
    )


async def rate(
    session: AsyncSession, access: TokenAccess, place_id: UUID, data: RatingUpdate
) -> VotePlace:
    """Store the person's rating of a place and return the place with it.

    Args:
        session: Open session.
        access: The checked token.
        place_id: Catalog place.
        data: Value and, for ``dont_want``, the reason.

    Returns:
        The place as the person sees it now.

    Raises:
        VoteLinkDeadError: The link no longer works.
        FeedbackPlaceNotFoundError: The place is not in the catalog.
    """
    await _check_link(session, access)
    rating: RatingRead = await feedback_service.rate_by_author(
        session, _actor(access), place_id, data
    )
    return await _place(session, access, rating.place_id)


async def _recompute(session: AsyncSession, membership: TripMembership) -> None:
    # A trip that cannot be planned yet (no city, dates or people) keeps the veto
    # for the next plan; the person's answer is stored either way.
    try:
        await plan_service.generate_plan(session, membership, None)
    except PlanInputError:
        return


async def veto(session: AsyncSession, access: TokenAccess, place_id: UUID) -> VotePlace:
    """File the person's veto (once per place) and recompute the plan.

    Args:
        session: Open session.
        access: The checked token.
        place_id: Catalog place.

    Returns:
        The place as the person sees it now.

    Raises:
        VoteLinkDeadError: The link no longer works.
        FeedbackPlaceNotFoundError: The place is not in the catalog.
    """
    membership, _ = await _check_link(session, access)
    stored: VetoRead = await feedback_service.veto_by_author(
        session, _actor(access), place_id
    )
    await _recompute(session, membership)
    return await _place(session, access, stored.place_id)


async def revoke_veto(
    session: AsyncSession, access: TokenAccess, veto_id: UUID
) -> VotePlace:
    """Withdraw the person's own veto and recompute the plan.

    Args:
        session: Open session.
        access: The checked token.
        veto_id: Veto from the session.

    Returns:
        The place as the person sees it now.

    Raises:
        VoteLinkDeadError: The link no longer works.
        VetoNotFoundError: No such veto of this person.
    """
    membership, _ = await _check_link(session, access)
    revoked = await feedback_service.revoke_veto_by_author(
        session, _actor(access), veto_id
    )
    await _recompute(session, membership)
    return await _place(session, access, revoked.place_id)
