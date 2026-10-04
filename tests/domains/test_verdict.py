"""Verdict per place (backend#51, an extension outside docs/algorytm.md v1.0)."""

from uuid import uuid5

import pytest

from tests.fixtures.city import place_id, places
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tuttitrip.places.schemas import PlaceCategory, PlaceRead
from tuttitrip.planning.logic.hard_constraints import RejectionCode
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.logic.utility import effort, explain, match
from tuttitrip.planning.plans.logic.verdict import build_verdicts, weighted_opinion
from tuttitrip.planning.plans.schemas import PlanVerdict, VerdictKind
from tuttitrip.planning.schemas import PlanningInput, PlanningPerson
from tuttitrip.profiles.feedback.schemas import ReasonCode

CATALOG = places()
DATA = planning_input(reference(), lodging=False)
TY, KASIA, TOMEK, BABCIA = DATA.people
MUSEUM = CATALOG["muzeum_miejskie"]


def trio(
    *votes: dict[str, int], weights: tuple[float, ...] = (1, 1, 1)
) -> PlanningInput:
    """Three people with the given votes on the museum, on a one-place catalog."""
    people = tuple(
        TY.model_copy(
            update={
                "id": uuid5(TY.id, str(i)),
                "weight": weights[i],
                "votes": {MUSEUM.id: vote["museum"]} if vote else {},
                "vote_reasons": {MUSEUM.id: ReasonCode.TOO_FAR}
                if vote.get("museum") == -1
                else {},
                "interests": {},
                "stairs_sensitivity": 0.0,
                "segment_km": 5.0,
            }
        )
        for i, vote in enumerate(votes)
    )
    return DATA.model_copy(
        update={"people": people, "places": (MUSEUM,), "must": frozenset()}
    )


def verdict_of(
    data: PlanningInput, key: str, plan: tuple[str, ...] = ()
) -> PlanVerdict:
    ids = [place_id(k) for k in plan]
    return next(v for v in build_verdicts(data, ids) if v.place_id == place_id(key))


def test_two_for_one_against_with_a_reason_fits() -> None:
    data = trio({"museum": 1}, {"museum": -1}, {"museum": 1})
    verdict = verdict_of(data, "muzeum_miejskie")
    assert verdict.verdict is VerdictKind.FITS
    assert len(verdict.yes) == 2
    assert [(n.profile_id, n.reason_code) for n in verdict.no] == [
        (data.people[1].id, ReasonCode.TOO_FAR)
    ]
    assert verdict.v_p == pytest.approx(1 / 3)
    assert verdict.substitute_place_id is None  # nothing else to propose


def test_a_veto_is_a_skip_with_its_code() -> None:
    verdict = verdict_of(DATA, "restauracja_morska")
    assert verdict.verdict is VerdictKind.SKIP
    assert RejectionCode.VETO.value in verdict.skip_codes
    assert [n.profile_id for n in verdict.no] == [BABCIA.id]


def test_an_e0_rejection_is_a_skip_with_the_code_and_person() -> None:
    verdict = verdict_of(DATA, "wieza_widokowa")  # stairs * sensitivity >= 0.9
    assert verdict.verdict is VerdictKind.SKIP
    assert "stairs" in verdict.skip_codes
    assert any(
        n.profile_id == BABCIA.id and n.reason_code is ReasonCode.TOO_HARD_FOR_CHILD
        for n in verdict.no
    )


def test_a_host_block_is_a_skip_and_a_must_is_a_must() -> None:
    blocked = DATA.model_copy(update={"blocked": frozenset({MUSEUM.id})})
    assert verdict_of(blocked, "muzeum_miejskie").verdict is VerdictKind.SKIP
    assert "blocked" in verdict_of(blocked, "muzeum_miejskie").skip_codes
    forced = DATA.model_copy(update={"must": frozenset({MUSEUM.id})})
    assert verdict_of(forced, "muzeum_miejskie").verdict is VerdictKind.MUST


def test_iconic_but_not_yours() -> None:
    iconic = MUSEUM.model_copy(update={"iconic": True})
    data = trio({}, {}, {}).model_copy(update={"places": (iconic,)})
    # No votes and no interests: V_p = 0, inside [-0.3, 0.1).
    assert verdict_of(data, "muzeum_miejskie").verdict is VerdictKind.ICONIC_NOT_YOURS
    plain = trio({}, {}, {}).model_copy(
        update={"places": (MUSEUM.model_copy(update={"iconic": False}),)}
    )
    assert verdict_of(plain, "muzeum_miejskie").verdict is VerdictKind.SKIP
    disliked = trio({"museum": -1}, {"museum": -1}, {"museum": 1}).model_copy(
        update={"places": (iconic,)}
    )
    assert (
        verdict_of(disliked, "muzeum_miejskie").verdict is VerdictKind.SKIP
    )  # V_p = -1/3


def test_a_place_in_the_plan_fits_whatever_the_opinion() -> None:
    data = trio({"museum": -1}, {"museum": -1}, {"museum": -1})
    assert verdict_of(data, "muzeum_miejskie", plan=("muzeum_miejskie",)).verdict is (
        VerdictKind.FITS
    )


def test_weights_decide_the_opinion() -> None:
    data = trio({"museum": 1}, {"museum": -1}, {"museum": -1}, weights=(3, 1, 1))
    assert weighted_opinion(data.people, MUSEUM, DEFAULT_PARAMS) == pytest.approx(1 / 5)


def test_the_substitute_is_the_best_other_place_of_the_category() -> None:
    verdicts = {v.place_id: v for v in build_verdicts(DATA, [])}
    by_id = {p.id: p for p in DATA.places}
    zoo = verdicts[place_id("zoo")]
    assert zoo.substitute_place_id is not None
    substitute = by_id[zoo.substitute_place_id]
    assert substitute.category is by_id[place_id("zoo")].category
    assert substitute.id != place_id("zoo")
    # The best by sum of w_i * u_ip among the accepted places of that category.
    assert not verdicts[substitute.id].skip_codes


def test_a_substitute_is_never_a_rejected_place_or_one_already_in_the_plan() -> None:
    in_plan = ("hevelianum",)
    for verdict in build_verdicts(DATA, [place_id(k) for k in in_plan]):
        if verdict.substitute_place_id is None:
            continue
        assert verdict.substitute_place_id not in {place_id(k) for k in in_plan}
        target = next(
            v
            for v in build_verdicts(DATA, [])
            if v.place_id == verdict.substitute_place_id
        )
        assert not target.skip_codes


def test_verdicts_are_deterministic_and_cover_every_candidate() -> None:
    first = build_verdicts(DATA, [])
    second = build_verdicts(DATA, [])
    assert first == second
    candidates = {p.id for p in DATA.places if p.category is not PlaceCategory.LODGING}
    assert {v.place_id for v in first} == candidates
    assert [str(v.place_id) for v in first] == sorted(str(v.place_id) for v in first)


def test_explain_cards_are_the_e1_numbers() -> None:
    person: PlanningPerson = TY
    place: PlaceRead = MUSEUM
    card = explain(person, place, has_lodging=False)
    assert card.match == match(person, place)
    assert card.effort == effort(person, place)
