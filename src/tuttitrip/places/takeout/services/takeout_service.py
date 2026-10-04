"""Import a Takeout list: parse, match to the trip city's catalog, rate."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.places.services import place_service
from tuttitrip.places.takeout.logic.name_match import (
    Candidate,
    MatchOutcome,
    match_title,
    normalise,
)
from tuttitrip.places.takeout.logic.takeout_csv import TakeoutRow, parse_takeout
from tuttitrip.places.takeout.schemas import (
    TakeoutImportRead,
    TakeoutMatched,
    TakeoutSkipReason,
    TakeoutUnmatched,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.trips.schemas import TripMembership
from tuttitrip.trips.services import trip_service


class TakeoutTripError(Exception):
    """The trip has no city, so there is no catalog to match against."""


_OUTCOME_REASON = {
    MatchOutcome.NOT_IN_CATALOG: TakeoutSkipReason.NOT_IN_CATALOG,
    MatchOutcome.AMBIGUOUS: TakeoutSkipReason.AMBIGUOUS,
}


def _classify(
    rows: Sequence[TakeoutRow], places: Sequence[PlaceRead]
) -> tuple[list[tuple[TakeoutRow, PlaceRead]], list[TakeoutUnmatched]]:
    by_id = {p.id: p for p in places}
    candidates = [Candidate(p.id, p.name) for p in places]
    seen: set[str] = set()
    taken: set[UUID] = set()
    matched: list[tuple[TakeoutRow, PlaceRead]] = []
    unmatched: list[TakeoutUnmatched] = []
    for row in rows:
        key = normalise(row.title)
        if not key:
            reason = TakeoutSkipReason.MISSING_TITLE
        elif key in seen:
            reason = TakeoutSkipReason.DUPLICATE
        else:
            seen.add(key)
            found = match_title(row.title, candidates)
            if found.place_id is None:
                reason = _OUTCOME_REASON[found.outcome]
            elif found.place_id in taken:
                reason = TakeoutSkipReason.DUPLICATE
            else:
                taken.add(found.place_id)
                matched.append((row, by_id[found.place_id]))
                continue
        unmatched.append(
            TakeoutUnmatched(line=row.line, title=row.title, reason=reason)
        )
    return matched, unmatched


async def import_takeout(
    session: AsyncSession, membership: TripMembership, profile_id: UUID, data: bytes
) -> TakeoutImportRead:
    """Match a Takeout list to the trip city's catalog and mark the hits as wanted.

    Places the person has already rated keep their vote. Two titles that give
    the same place count once (the second is a ``duplicate``).

    Args:
        session: Open session.
        membership: Proof from ``TripAccess`` (co-host or host).
        profile_id: The person whose list it is.
        data: The uploaded CSV.

    Returns:
        What matched, what did not and why.

    Raises:
        TakeoutFormatError: The file is not a Takeout list (from the parser).
        TakeoutTripError: The trip has no city.
    """
    rows = parse_takeout(data)
    trip = await trip_service.get_trip(session, membership)
    if trip.city_slug is None:
        msg = "The trip needs a city to match the list against"
        raise TakeoutTripError(msg)
    places = await place_service.list_all_places(session, trip.city_slug)
    pairs, unmatched = _classify(rows, places)
    liked = await feedback_service.like_places(
        session, membership, profile_id, [place.id for _, place in pairs]
    )
    return TakeoutImportRead(
        profile_id=profile_id,
        city_slug=trip.city_slug,
        total=len(rows),
        liked=len(liked),
        kept=len(pairs) - len(liked),
        matched=[
            TakeoutMatched(
                line=row.line,
                title=row.title,
                place_id=place.id,
                place_name=place.name,
                liked=place.id in liked,
            )
            for row, place in pairs
        ],
        unmatched=unmatched,
    )
