"""The host's view of the voting: who said what about each place."""

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places.services import place_service
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.schemas import TripMembership
from tuttitrip.voting.logic import summary
from tuttitrip.voting.schemas import PlaceVoteSummary, VoteSummaryQuery


async def get_summary(
    session: AsyncSession, membership: TripMembership, query: VoteSummaryQuery
) -> Page[PlaceVoteSummary]:
    """One page of per-place results from the trip's ratings and active vetoes.

    The trip is small (tens of places), so the summary is built in memory and
    then filtered, sorted and cut into the requested page.

    Args:
        session: Open session.
        membership: The caller's checked (co-host) membership.
        query: Page, sort and filters.

    Returns:
        The page; a page past the end is empty with the real total.
    """
    feedback = await feedback_service.list_for_trip(session, membership.trip_id)
    profiles = await profile_service.list_profiles(session, membership)
    place_ids = {r.place_id for r in feedback.ratings} | {
        v.place_id for v in feedback.vetoes
    }
    places = await place_service.get_places(session, place_ids)
    people = {
        p.id: summary.Person(display_name=p.display_name, user_sub=p.user_sub)
        for p in profiles
    }
    rows = summary.summarize(
        feedback.ratings,
        feedback.vetoes,
        people,
        {place_id: place.name for place_id, place in places.items()},
    )
    chosen = summary.ordered(
        [row for row in rows if summary.matches(row, query)], query.sort, query.dir
    )
    return Page[PlaceVoteSummary].of(
        chosen[query.offset : query.offset + query.size], len(chosen), query
    )
