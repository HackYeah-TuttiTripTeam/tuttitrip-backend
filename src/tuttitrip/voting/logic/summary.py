"""The host's aggregate of ratings and vetoes, per place (pure)."""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from tuttitrip.profiles.feedback.schemas import (
    LINK_AUTHOR_PREFIX,
    RatingRead,
    RatingValue,
    VetoRead,
)
from tuttitrip.shared.pagination.schemas import SortDir
from tuttitrip.voting.schemas import (
    PersonVeto,
    PersonVote,
    PlaceVoteSummary,
    VoteSource,
    VoteSummaryFilters,
    VoteSummarySort,
)

UNKNOWN_PERSON = "?"


@dataclass(frozen=True, slots=True)
class Person:
    """What the summary needs to know about a profile."""

    display_name: str
    user_sub: str | None


def rating_source(rating: RatingRead, person: Person | None) -> VoteSource:
    """Tell who stands behind a rating.

    Args:
        rating: The stored rating.
        person: Its profile (None when unknown).

    Returns:
        `link` for a voting link, `app` when the person rated themselves,
        otherwise `host` (someone else rated on their behalf).
    """
    if rating.updated_by_sub.startswith(LINK_AUTHOR_PREFIX):
        return VoteSource.LINK
    if person is not None and rating.updated_by_sub == person.user_sub:
        return VoteSource.APP
    return VoteSource.HOST


def veto_source(veto: VetoRead) -> VoteSource:
    """Tell who stands behind a veto.

    Args:
        veto: The stored veto.

    Returns:
        `link`, `host` (filed on someone's behalf) or `app`.
    """
    if veto.created_by_sub.startswith(LINK_AUTHOR_PREFIX):
        return VoteSource.LINK
    return VoteSource.HOST if veto.on_behalf else VoteSource.APP


def _name(people: Mapping[UUID, Person], profile_id: UUID) -> str:
    person = people.get(profile_id)
    return person.display_name if person else UNKNOWN_PERSON


def summarize(
    ratings: Sequence[RatingRead],
    vetoes: Sequence[VetoRead],
    people: Mapping[UUID, Person],
    place_names: Mapping[UUID, str],
) -> list[PlaceVoteSummary]:
    """Group ratings and active vetoes by place.

    Only places somebody voted on or vetoed appear. Counts are unweighted: the
    weights `w_i` belong to the plan verdict, not to this who-said-what view.

    Args:
        ratings: Every rating of the trip.
        vetoes: Active vetoes of the trip.
        people: Profiles by id.
        place_names: Catalog names by place id.

    Returns:
        One summary per place, by place name then id.
    """
    votes: dict[UUID, list[PersonVote]] = defaultdict(list)
    blocks: dict[UUID, list[PersonVeto]] = defaultdict(list)
    for rating in ratings:
        votes[rating.place_id].append(
            PersonVote(
                profile_id=rating.profile_id,
                display_name=_name(people, rating.profile_id),
                value=rating.value,
                reason_code=rating.reason_code,
                source=rating_source(rating, people.get(rating.profile_id)),
                updated_at=rating.updated_at,
            )
        )
    for veto in vetoes:
        blocks[veto.place_id].append(
            PersonVeto(
                veto_id=veto.id,
                profile_id=veto.profile_id,
                display_name=_name(people, veto.profile_id),
                source=veto_source(veto),
                created_at=veto.created_at,
            )
        )
    result = [
        PlaceVoteSummary(
            place_id=place_id,
            place_name=place_names.get(place_id, UNKNOWN_PERSON),
            want=sum(v.value is RatingValue.WANT for v in votes[place_id]),
            dont_want=sum(v.value is RatingValue.DONT_WANT for v in votes[place_id]),
            neutral=sum(v.value is RatingValue.NEUTRAL for v in votes[place_id]),
            veto_count=len(blocks[place_id]),
            votes=votes[place_id],
            vetoes=blocks[place_id],
        )
        for place_id in votes.keys() | blocks.keys()
    ]
    return sorted(result, key=lambda s: (s.place_name.casefold(), s.place_id))


def matches(summary: PlaceVoteSummary, filters: VoteSummaryFilters) -> bool:
    """Tell whether a place passes the list filters.

    Args:
        summary: One place's summary.
        filters: The query's filters.

    Returns:
        True when it should be listed.
    """
    if filters.has_veto is not None and (summary.veto_count > 0) != filters.has_veto:
        return False
    if filters.source is None:
        return True
    return any(v.source is filters.source for v in summary.votes) or any(
        v.source is filters.source for v in summary.vetoes
    )


_KEYS: dict[VoteSummarySort, Callable[[PlaceVoteSummary], object]] = {
    VoteSummarySort.NAME: lambda s: s.place_name.casefold(),
    VoteSummarySort.WANT: lambda s: s.want,
    VoteSummarySort.DONT_WANT: lambda s: s.dont_want,
    VoteSummarySort.VETO: lambda s: s.veto_count,
}


def ordered(
    items: list[PlaceVoteSummary], sort: VoteSummarySort, direction: SortDir
) -> list[PlaceVoteSummary]:
    """Sort places by a key, the place id last so pages are stable.

    Args:
        items: Summaries to sort.
        sort: Sort key.
        direction: Ascending or descending (the id ties follow it too).

    Returns:
        A new sorted list.
    """
    key = _KEYS[sort]
    return sorted(
        items,
        key=lambda s: (key(s), s.place_id),
        reverse=direction is SortDir.DESC,
    )
