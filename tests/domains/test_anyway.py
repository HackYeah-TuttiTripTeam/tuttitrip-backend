"""The daily "anyway" suggestion (backend#100): choice, cost, rejection, template."""

import asyncio
from collections.abc import Mapping
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import tuttitrip.planning.anyway.logic.suggest as module
from tests.fixtures.city import place_id
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tests.shared.fakes import FakeJobQueue
from tuttitrip.places.schemas import PlaceCategory
from tuttitrip.planning.anyway.logic.constants import CANDIDATES_PER_DAY
from tuttitrip.planning.anyway.logic.suggest import suggest, template_text
from tuttitrip.planning.anyway.services import anyway_service
from tuttitrip.planning.logic.hard_constraints import filter_places
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.plan_group import GroupPlan, plan_group
from tuttitrip.planning.logic.solver import Solver
from tuttitrip.planning.plans.logic.verdict import build_verdicts
from tuttitrip.planning.plans.schemas import (
    AnywayEffects,
    AnywaySuggestion,
    PlanVerdict,
    VerdictKind,
)
from tuttitrip.planning.schemas import PlanningInput
from tuttitrip.shared.jobs.contracts import Workflow, WriteJustificationsInput
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError

FULL = planning_input(reference(), lodging=False)
# One day is too short for every place: the plan leaves some out.
DATA = FULL.model_copy(
    update={"trip": FULL.trip.model_copy(update={"days": FULL.trip.days[:1]})}
)


@pytest.fixture(scope="module")
def base() -> GroupPlan:
    return plan_group(DATA)


def outside(base: GroupPlan) -> list[UUID]:
    # Places E0 accepts that the plan left out, in id order.
    in_plan = set(base.plan.place_ids)
    accepted = filter_places(DATA, DEFAULT_PARAMS).accepted
    return sorted(
        (
            p.id
            for p in accepted
            if p.id not in in_plan and p.category is not PlaceCategory.LODGING
        ),
        key=str,
    )


def iconic(place: UUID, v_p: float = 0.0) -> PlanVerdict:
    return PlanVerdict(
        place_id=place,
        verdict=VerdictKind.ICONIC_NOT_YOURS,
        v_p=v_p,
        justification="x",
        justification_source="template",
    )


def test_a_candidate_gets_a_suggestion_with_the_cost_of_adding_it(
    base: GroupPlan,
) -> None:
    candidates = [iconic(p) for p in outside(base)]
    found = suggest(DATA, base, candidates)
    assert 1 <= len(found) <= len(base.plan.days)
    assert len({s.day for s in found}) == len(found)  # one per day
    assert [s.day for s in found] == sorted(s.day for s in found)
    for item in found:
        assert 1 <= item.day <= len(base.plan.days)
        assert item.justification_source == "template"
        assert str(item.effects.d_minutes) in item.justification.replace("+", "")
        assert item.place_id not in set(base.plan.place_ids)


def test_the_cost_is_the_plan_with_the_place_as_a_must_minus_the_plan_without_it(
    base: GroupPlan,
) -> None:
    only = outside(base)[0]
    found = suggest(DATA, base, [iconic(only)])
    assert len(found) == 1
    with_place = plan_group(
        DATA.model_copy(update={"must": frozenset({only})}),
        u_star={r.person_id: r.u_star for r in base.people},
    )
    effects = found[0].effects
    assert only in with_place.plan.place_ids
    assert effects.d_cost == with_place.plan.cost.total - base.plan.cost.total
    assert effects.d_min_r == pytest.approx(with_place.min_r - base.min_r)


def test_the_same_data_gives_the_same_suggestion(base: GroupPlan) -> None:
    candidates = [iconic(p) for p in outside(base)]
    assert suggest(DATA, base, candidates) == suggest(DATA, base, list(candidates))


def test_the_order_of_the_verdicts_does_not_matter(base: GroupPlan) -> None:
    candidates = [iconic(p) for p in outside(base)]
    assert suggest(DATA, base, candidates) == suggest(DATA, base, candidates[::-1])


def test_no_iconic_verdict_means_no_suggestion(base: GroupPlan) -> None:
    verdicts = build_verdicts(DATA, base.plan.place_ids)
    assert not any(v.verdict is VerdictKind.ICONIC_NOT_YOURS for v in verdicts)
    assert suggest(DATA, base, verdicts) == ()


def test_a_rejected_place_does_not_come_back_on_its_day(base: GroupPlan) -> None:
    candidates = [iconic(p) for p in outside(base)]
    first = suggest(DATA, base, candidates)[0]
    again = suggest(DATA, base, candidates, rejected={(first.day, first.place_id)})
    assert (first.day, first.place_id) not in {(s.day, s.place_id) for s in again}


def test_the_work_is_bounded_by_the_days(
    base: GroupPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[int] = []

    real = module.plan_group

    def counting(
        data: PlanningInput,
        params: AlgorithmParams,
        *,
        alpha: float,
        u_star: Mapping[UUID, float] | None,
        solver: Solver,
    ) -> GroupPlan:
        runs.append(1)
        return real(data, params, alpha=alpha, u_star=u_star, solver=solver)

    monkeypatch.setattr(module, "plan_group", counting)
    candidates = [iconic(p) for p in outside(base)]
    assert len(candidates) > len(base.plan.days)
    suggest(DATA, base, candidates)
    assert len(runs) <= len(base.plan.days) * CANDIDATES_PER_DAY


def test_a_place_the_solver_cannot_add_is_skipped(base: GroupPlan) -> None:
    vetoed = place_id("restauracja_morska")  # E0 rejects it: it cannot be a must
    assert suggest(DATA, base, [iconic(vetoed)]) == ()


def test_the_template_carries_the_numbers(base: GroupPlan) -> None:
    item = suggest(DATA, base, [iconic(outside(base)[0], 0.12)])[0]
    text = template_text(item.day, 0.12, item.effects, "PLN")
    assert "+0.12" in text
    assert f"{item.effects.d_cost:+.2f} PLN" in text
    assert f"{item.effects.d_minutes:+d} min" in text


class _Session:
    """The service only hands the session to the worker check, which is patched."""


def _suggestion(place: UUID) -> AnywaySuggestion:
    return AnywaySuggestion(
        place_id=place,
        name="Muzeum",
        day=1,
        v_p=0.0,
        effects=AnywayEffects(d_min_r=0.0, d_cost=Decimal(0), d_minutes=0),
        justification="x",
        justification_source="template",
    )


async def _ready(_: object) -> None:  # ruff: ignore[unused-async] the awaited stub
    return None


async def _missing(_: object) -> None:  # ruff: ignore[unused-async] the awaited stub
    raise WorkerUnavailableError


def test_the_worker_is_asked_once_per_version_for_the_texts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(anyway_service, "ensure_worker_available", _ready)
    queue = FakeJobQueue()
    plan_id, place = uuid4(), uuid4()

    async def ask() -> bool:
        return await anyway_service.request_justifications(
            cast("AsyncSession", _Session()), queue, plan_id, [_suggestion(place)]
        )

    assert asyncio.run(ask()) is True
    assert asyncio.run(ask()) is True
    (job,) = queue.jobs.values()  # the same deterministic id
    assert job.workflow_name == Workflow.WRITE_JUSTIFICATIONS.value
    (payload,) = queue.payloads.values()
    assert isinstance(payload, WriteJustificationsInput)
    assert payload.plan_id == plan_id


def test_nothing_is_asked_without_a_suggestion_or_a_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FakeJobQueue()
    session = cast("AsyncSession", _Session())
    plan_id = uuid4()
    nothing = anyway_service.request_justifications(session, queue, plan_id, [])
    assert asyncio.run(nothing) is False
    monkeypatch.setattr(anyway_service, "ensure_worker_available", _missing)
    down = anyway_service.request_justifications(
        session, queue, plan_id, [_suggestion(uuid4())]
    )
    assert asyncio.run(down) is False  # the template stays
    assert queue.jobs == {}
