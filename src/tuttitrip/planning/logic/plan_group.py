"""The whole computation of a group plan (docs/algorytm.md, E4 and E5, section 4).

Order of work: the solo runs (one per person, in id order), the effective floors
``f_i^eff = min(f_i, 0.6 * u*_i)``, the group plan from the solver, then
``r_i``, ``min r`` and Jain's index. For ``n = 1`` there is nobody to protect
from: no solo run, ``u* = u``, ``r = 1`` and ``f^eff = 0``, and the weakest
domain replaces the Jain index in the UI (section 4).
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from tuttitrip.planning.fairness.logic.measure import jain, min_r
from tuttitrip.planning.fairness.logic.violations import effective_floor
from tuttitrip.planning.logic.domains import RequirementOutcome
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.reference import relative_satisfaction, solo_utility
from tuttitrip.planning.logic.solver import PlanResult, solve
from tuttitrip.planning.schemas import DomainScores, LodgingStay, PlanningInput
from tuttitrip.profiles.preferences.schemas import ImportanceDomain


@dataclass(frozen=True, slots=True)
class PersonReference:
    """One row of the fairness ledger."""

    person_id: UUID
    u_star: float
    u: float
    r: float
    floor: float
    floor_eff: float
    floor_met: bool
    weakest_domain: ImportanceDomain | None
    """Only for ``n = 1``: the domain the plan serves worst."""


@dataclass(frozen=True, slots=True)
class GroupPlan:
    """A group plan with the reference points and the fairness measures."""

    plan: PlanResult
    people: tuple[PersonReference, ...]
    jain: float
    min_r: float
    solo_runs: int
    solo_elapsed_ms: int


_FLOOR_TOLERANCE = 1e-9


def _weakest(scores: DomainScores) -> ImportanceDomain:
    # Lowest q over the applicable domains; ties go to the first in enum order.
    q = {
        ImportanceDomain.LODGING: scores.lodging,
        ImportanceDomain.FOOD: scores.food,
        ImportanceDomain.ATTRACTIONS: scores.attractions,
        ImportanceDomain.PACE: scores.pace,
        ImportanceDomain.COST: scores.cost,
    }
    applicable = {d: v for d, v in q.items() if v is not None}
    return min(applicable, key=lambda d: applicable[d])


def plan_group(  # ruff: ignore[too-many-arguments] the whole input of a group plan
    data: PlanningInput,
    params: AlgorithmParams = DEFAULT_PARAMS,
    *,
    alpha: float = 1.0,
    lodging: LodgingStay | None = None,
    lodging_outcomes: Sequence[RequirementOutcome] | None = None,
    max_evaluations: int | None = None,
) -> GroupPlan:
    """Solo runs, floors, the group plan and the fairness measures.

    Args:
        data: The planning input.
        params: Algorithm parameters.
        alpha: Fairness slider in 0 to 3.
        lodging: The lodging base, given exactly when the trip has nights.
        lodging_outcomes: The trip's lodging requirements checked against it.
        max_evaluations: Work limit of every run (solo and group); default
            scales with the instance.

    Returns:
        The plan, a ledger row per person (id order), ``min r`` and Jain's index.
    """
    people = sorted(data.people, key=lambda p: str(p.id))
    alone = len(people) == 1
    started = time.perf_counter()
    u_star: dict[UUID, float] = {}
    if not alone:
        for person in people:
            run = solo_utility(
                data,
                person,
                params,
                alpha=alpha,
                lodging=lodging,
                lodging_outcomes=lodging_outcomes,
                max_evaluations=max_evaluations,
            )
            u_star[person.id] = run.scores[0].welfare
    solo_ms = int((time.perf_counter() - started) * 1000)
    floors = {
        p.id: 0.0 if alone else effective_floor(p.floor, u_star[p.id], params)
        for p in people
    }
    plan = solve(
        data,
        params,
        alpha=alpha,
        lodging=lodging,
        lodging_outcomes=lodging_outcomes,
        floors=floors,
        max_evaluations=max_evaluations,
    )
    by_person = {s.person_id: s for s in plan.scores}
    rows = tuple(
        PersonReference(
            person_id=p.id,
            u_star=by_person[p.id].welfare if alone else u_star[p.id],
            u=by_person[p.id].welfare,
            r=1.0
            if alone
            else relative_satisfaction(by_person[p.id].welfare, u_star[p.id], params),
            floor=p.floor,
            floor_eff=floors[p.id],
            floor_met=by_person[p.id].welfare >= floors[p.id] - _FLOOR_TOLERANCE,
            weakest_domain=_weakest(by_person[p.id]) if alone else None,
        )
        for p in people
    )
    r = [row.r for row in rows]
    return GroupPlan(
        plan=plan,
        people=rows,
        jain=jain(r),
        min_r=min_r(r),
        solo_runs=0 if alone else len(people),
        solo_elapsed_ms=solo_ms,
    )
