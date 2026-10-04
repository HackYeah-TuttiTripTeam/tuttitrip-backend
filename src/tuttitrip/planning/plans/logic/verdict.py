"""Verdict on every candidate place (backend#51; EXTENSION, outside v1.0).

The final specification (docs/algorytm.md) defines no verdict rules, only
``explain()`` (section 10). These rules come from the old attachment and are
marked as an extension; the thresholds live in ``AlgorithmParams``.

```
skip     a veto, a host block or an E0 rejection (with its code)
must     the host forced it (override)
fits     the place is in the plan, or V_p >= 0.1
iconic   -0.3 <= V_p < 0.1 and the catalog marks the place iconic
V_p      sum_i w_i v_ip / sum_i w_i
v_ip     the vote, else +1 when m_ip >= 0.6, else 0
```

"For" and "against" lists carry the reason codes. A person without a vote and
with ``m_ip >= 0.6`` counts as "fits me" (the same threshold as the "own place" of
E5). The substitute is the best place outside the plan of the same category that
passes E0 for everybody, by ``sum_i w_i u_ip``; none gives ``null``. Pure.
"""

from collections.abc import Iterable, Sequence
from uuid import UUID

from tuttitrip.places.schemas import PlaceCategory, PlaceRead
from tuttitrip.planning.logic.hard_constraints import (
    Rejection,
    RejectionCode,
    filter_places,
)
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.utility import match, utility
from tuttitrip.planning.plans.logic.justification import template_justification
from tuttitrip.planning.plans.schemas import PlanVerdict, VerdictKind, VoteReason
from tuttitrip.planning.schemas import PlanningInput, PlanningPerson
from tuttitrip.profiles.feedback.schemas import ReasonCode

_REJECTION_REASON: dict[RejectionCode, ReasonCode] = {
    RejectionCode.VETO: ReasonCode.OTHER,
    RejectionCode.SEGMENT: ReasonCode.TOO_FAR,
    RejectionCode.STAIRS: ReasonCode.TOO_HARD_FOR_CHILD,
}


def _opinion(
    person: PlanningPerson, place: PlaceRead, params: AlgorithmParams
) -> float:
    # The vote, or +1 for "fits me" without a vote, else 0.
    vote = person.votes.get(place.id)
    if vote is not None:
        return float(vote)
    return 1.0 if match(person, place, params) >= params.own_place_match else 0.0


def weighted_opinion(
    people: Iterable[PlanningPerson], place: PlaceRead, params: AlgorithmParams
) -> float:
    """``V_p``: the weighted mean of the people's opinions of a place.

    Args:
        people: Everybody on the trip.
        place: The place.
        params: Algorithm parameters (``own_place_match``).

    Returns:
        A value in -1 to +1.
    """
    members = list(people)
    total = sum(p.weight for p in members)
    return sum(p.weight * _opinion(p, place, params) for p in members) / total


def _sides(
    people: Sequence[PlanningPerson],
    place: PlaceRead,
    rejections: Sequence[Rejection],
    params: AlgorithmParams,
) -> tuple[list[VoteReason], list[VoteReason]]:
    rejected = {
        r.person_id: _REJECTION_REASON[r.code]
        for r in rejections
        if r.person_id is not None and r.code in _REJECTION_REASON
    }
    yes: list[VoteReason] = []
    no: list[VoteReason] = []
    for person in people:
        vote = person.votes.get(place.id)
        if person.id in rejected:
            no.append(VoteReason(profile_id=person.id, reason_code=rejected[person.id]))
        elif vote == -1:
            no.append(
                VoteReason(
                    profile_id=person.id, reason_code=person.vote_reasons.get(place.id)
                )
            )
        elif vote == 1 or (
            vote is None and match(person, place, params) >= params.own_place_match
        ):
            yes.append(VoteReason(profile_id=person.id))
    return yes, no


def _substitutes(
    data: PlanningInput,
    accepted: Sequence[PlaceRead],
    in_plan: set[UUID],
    params: AlgorithmParams,
) -> dict[PlaceCategory, list[tuple[float, str, UUID]]]:
    # Per category the places outside the plan, best sum of w_i * u_ip first.
    by_category: dict[PlaceCategory, list[tuple[float, str, UUID]]] = {}
    for place in accepted:
        if place.id in in_plan:
            continue
        score = sum(
            p.weight
            * utility(p, place, has_lodging=data.trip.has_lodging, params=params)
            for p in data.people
        )
        by_category.setdefault(place.category, []).append(
            (-score, str(place.id), place.id)
        )
    for options in by_category.values():
        options.sort()
    return by_category


def build_verdicts(
    data: PlanningInput,
    plan_place_ids: Iterable[UUID],
    params: AlgorithmParams = DEFAULT_PARAMS,
) -> list[PlanVerdict]:
    """A verdict for every candidate place, in place id order.

    Args:
        data: The planning input (people, places, "must", blocks).
        plan_place_ids: The places of the chosen plan.
        params: Algorithm parameters (the ``V_p`` thresholds).

    Returns:
        One ``PlanVerdict`` per non-lodging place.
    """
    in_plan = set(plan_place_ids)
    filtered = filter_places(data, params)
    accepted = {p.id for p in filtered.accepted}
    people = sorted(data.people, key=lambda p: str(p.id))
    substitutes = _substitutes(data, filtered.accepted, in_plan, params)
    verdicts: list[PlanVerdict] = []
    for place in sorted(data.places, key=lambda p: str(p.id)):
        if place.category is PlaceCategory.LODGING:
            continue
        rejections = filtered.reasons(place.id)
        yes, no = _sides(people, place, rejections, params)
        v_p = weighted_opinion(people, place, params)
        if place.id not in accepted:
            kind = VerdictKind.SKIP
        elif place.id in data.must:
            kind = VerdictKind.MUST
        elif place.id in in_plan or v_p >= params.verdict_fits:
            kind = VerdictKind.FITS
        elif place.iconic and v_p >= params.verdict_iconic:
            kind = VerdictKind.ICONIC_NOT_YOURS
        else:
            kind = VerdictKind.SKIP
        codes = sorted({r.code.value for r in rejections})
        options = [] if place.id in in_plan else substitutes.get(place.category, [])
        substitute = next((pid for _, _, pid in options if pid != place.id), None)
        verdicts.append(
            PlanVerdict(
                place_id=place.id,
                verdict=kind,
                v_p=v_p,
                yes=yes,
                no=no,
                skip_codes=codes,
                substitute_place_id=substitute,
                justification=template_justification(
                    kind, len(yes), len(no), codes, "pl"
                ),
                justification_source="template",
            )
        )
    return verdicts
