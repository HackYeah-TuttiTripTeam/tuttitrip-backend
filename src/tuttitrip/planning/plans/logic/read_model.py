"""Map a computed group plan onto ``PlanRead`` content (docs/algorytm.md, section 10).

Pure: no database and no ids of the stored version. The service adds those. Money
is in the trip currency. The lodging domain is "not applicable" while the trip
has no lodging base (the choice of a base is backend#70), and a domain whose pool
weight ``a_ij`` is 0 is also not applicable.
"""

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.planning.fairness.logic.violations import days_without_own_place
from tuttitrip.planning.fairness.schemas import ConflictCode as FairnessConflict
from tuttitrip.planning.logic.cost import place_cost
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.logic.plan_group import GroupPlan, PersonReference
from tuttitrip.planning.logic.solver import PlannedDay, PlanResult, SolverConflict
from tuttitrip.planning.logic.utility import explain, match, place_domain
from tuttitrip.planning.plans.logic.input_builder import NO_BUDGET
from tuttitrip.planning.plans.schemas import (
    ApprovalStatus,
    BudgetZone,
    ConflictCode,
    Currency,
    ExplainEntry,
    FloorMiss,
    FloorMissKind,
    PersonFairness,
    PlaceKind,
    PlanBudget,
    PlanConflict,
    PlanDay,
    PlanDomainCode,
    PlanDomainScore,
    PlanFairness,
    PlanStop,
    PlanTelemetry,
    StopTransfer,
    TransferMode,
)
from tuttitrip.planning.schemas import (
    DayPlan,
    DomainScores,
    PlanningInput,
    PlanningPerson,
)
from tuttitrip.profiles.preferences.schemas import ImportanceDomain

_CENT = Decimal("0.01")
_DOMAIN_CODE = {
    ImportanceDomain.LODGING: PlanDomainCode.LODGING,
    ImportanceDomain.FOOD: PlanDomainCode.FOOD,
    ImportanceDomain.ATTRACTIONS: PlanDomainCode.ATTRACTIONS,
    ImportanceDomain.PACE: PlanDomainCode.PACE,
    ImportanceDomain.COST: PlanDomainCode.COST,
}
_CONFLICT_CODE = {
    SolverConflict.MUST_REJECTED: ConflictCode.VETO_BLOCKS_PLACE,
    SolverConflict.MUST_UNPLACEABLE: ConflictCode.BUDGET_LIMIT,
    SolverConflict.LODGING_OVER_CAP: ConflictCode.BUDGET_LIMIT,
}


def _unlimited(data: PlanningInput) -> bool:
    # A trip without a budget is priced against a sentinel B_do (never shown).
    return data.trip.budget_to >= NO_BUDGET


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, ROUND_HALF_UP)


def _domain_scores(
    person: PlanningPerson, scores: DomainScores, *, unlimited: bool
) -> list[PlanDomainScore]:
    pool = person.pool
    values = {
        ImportanceDomain.LODGING: (scores.lodging, pool.lodging),
        ImportanceDomain.FOOD: (scores.food, pool.food),
        ImportanceDomain.ATTRACTIONS: (scores.attractions, pool.attractions),
        ImportanceDomain.PACE: (scores.pace, pool.pace),
        ImportanceDomain.COST: (scores.cost, pool.cost),
    }
    return [
        PlanDomainScore(domain=_DOMAIN_CODE[d], q=None, not_applicable=True)
        if q is None or points == 0 or (unlimited and d is ImportanceDomain.COST)
        else PlanDomainScore(domain=_DOMAIN_CODE[d], q=q, not_applicable=False)
        for d, (q, points) in values.items()
    ]


def _weakest(domains: Sequence[PlanDomainScore]) -> PlanDomainCode | None:
    applicable = [d for d in domains if d.q is not None]
    if not applicable:
        return None
    return min(applicable, key=lambda d: d.q or 0.0).domain


def _stops(
    data: PlanningInput, plan: PlanResult, places: Mapping[UUID, PlaceRead]
) -> list[PlanDay]:
    people = data.people
    currency = data.trip.currency
    days: list[PlanDay] = []
    for index, planned in enumerate(plan.days, start=1):
        items: list[PlanStop] = []
        for number, visit in enumerate(planned.schedule.visits):
            place = places[visit.place_id]
            cost = place_cost(place, people, currency)
            rows = [r for r in place.prices if r.currency == currency]
            priced = bool(rows) and cost.complete
            share = Decimal(len(people))
            items.append(
                PlanStop(
                    place_id=place.id,
                    name=place.name,
                    kind=PlaceKind.FOOD
                    if place_domain(place) is ImportanceDomain.FOOD
                    else PlaceKind.ATTRACTION,
                    lat=place.lat,
                    lon=place.lon,
                    start=visit.start.time(),
                    end=visit.end.time(),
                    transfer=None
                    if number == 0
                    else StopTransfer(
                        minutes=visit.transfer_min, mode=TransferMode.WALK, cost=None
                    ),
                    cost_per_person=_money(cost.total / share) if priced else None,
                    price_base=_money(cost.base / share) if priced else None,
                    price_inflated=_money(cost.total / share) if priced else None,
                    price_verified=priced and all(r.verified for r in rows),
                    price_source_url=next(
                        (r.source_url for r in rows if r.source_url), None
                    ),
                    price_verified_at=next(
                        (r.checked_at for r in rows if r.checked_at), None
                    ),
                    hours_verified=place.hours.verified,
                    hours_source_url=place.hours.source_url,
                    hours_verified_at=place.hours.checked_at,
                    google_place_id=place.google_place_id,
                )
            )
        days.append(PlanDay(index=index, date=planned.day, items=items))
    return days


def _fairness(
    data: PlanningInput,
    group: GroupPlan,
    names: Mapping[UUID, str],
    places: Mapping[UUID, PlaceRead],
) -> PlanFairness:
    scores = {s.person_id: s for s in group.plan.scores}
    day_plans = [
        DayPlan(place_ids=tuple(v.place_id for v in d.schedule.visits))
        for d in group.plan.days
    ]
    people = {p.id: p for p in data.people}
    rows: list[PersonFairness] = []
    for row in group.people:
        person = people[row.person_id]
        domains = _domain_scores(
            person, scores[row.person_id], unlimited=_unlimited(data)
        )
        missing = days_without_own_place(person, day_plans, places)
        rows.append(
            PersonFairness(
                profile_id=row.person_id,
                name=names.get(row.person_id, ""),
                u=row.u,
                u_star=row.u_star,
                r=row.r,
                floor=row.floor,
                floor_eff=row.floor_eff,
                floor_met=row.floor_met,
                domains=domains,
                own_place_days=len(day_plans) - missing,
                weakest_domain=_weakest(domains),
            )
        )
    return PlanFairness(
        group_size=len(rows), jain=group.jain, min_r=group.min_r, per_person=rows
    )


def _floors_missed(
    group: GroupPlan, data: PlanningInput, places: Mapping[UUID, PlaceRead]
) -> list[FloorMiss]:
    by_person: dict[UUID, PersonReference] = {r.person_id: r for r in group.people}
    people = {p.id: p for p in data.people}
    result: list[FloorMiss] = []
    for conflict in group.plan.report.conflicts:
        row = by_person[conflict.person_id]
        if conflict.code is FairnessConflict.FLOOR:
            result.append(
                FloorMiss(
                    kind=FloorMissKind.FLOOR,
                    profile_id=conflict.person_id,
                    shortfall=max(0.0, row.floor_eff - row.u),
                )
            )
        elif conflict.code is FairnessConflict.OWN_PLACE:
            result.extend(
                FloorMiss(
                    kind=FloorMissKind.OWN_PLACE_DAY,
                    profile_id=conflict.person_id,
                    shortfall=1.0,
                    day=index,
                )
                for index, day in enumerate(group.plan.days, start=1)
                if not _has_own_place(people[conflict.person_id], day, places)
            )
        else:
            result.append(
                FloorMiss(
                    kind=FloorMissKind.TAG_MINIMUM,
                    profile_id=conflict.person_id,
                    shortfall=conflict.missing,
                    tag=conflict.tag,
                )
            )
    return result


def _has_own_place(
    person: PlanningPerson, day: PlannedDay, places: Mapping[UUID, PlaceRead]
) -> bool:
    return any(
        match(person, places[v.place_id]) >= DEFAULT_PARAMS.own_place_match
        for v in day.schedule.visits
    )


def _conflicts(
    data: PlanningInput, group: GroupPlan, plan: PlanResult
) -> list[PlanConflict]:
    found = [
        PlanConflict(
            reason_code=_CONFLICT_CODE[kind],
            place_id=place_id,
            profile_ids=sorted(
                (p.id for p in data.people if place_id in p.vetoes), key=str
            )
            if kind is SolverConflict.MUST_REJECTED
            else [],
        )
        for kind, place_id in plan.conflicts
    ]
    found.extend(
        PlanConflict(reason_code=ConflictCode.UNKNOWN_PRICE, place_id=pid)
        for pid in plan.cost.unknown_price_place_ids
    )
    found.extend(
        PlanConflict(
            reason_code=ConflictCode.FLOOR_UNREACHABLE,
            profile_ids=[row.person_id],
            params={"floor": row.floor, "floor_eff": row.floor_eff},
        )
        for row in group.people
        if row.floor_eff < row.floor
    )
    return found


def _budget(data: PlanningInput, plan: PlanResult) -> PlanBudget:
    trip = data.trip
    cost = plan.cost.total
    unlimited = _unlimited(data)
    if cost <= trip.budget_from:
        zone = BudgetZone.BELOW_B_FROM
    elif unlimited or cost <= trip.budget_to:
        zone = BudgetZone.UP_TO_B_TO
    else:
        zone = BudgetZone.IN_MARGIN
    return PlanBudget(
        currency=Currency(trip.currency),
        cost=cost,
        b_from=_money(trip.budget_from),
        b_to=None if unlimited else _money(trip.budget_to),
        b_max=None if unlimited else _money(trip.budget_max),
        unlimited=unlimited,
        zone=zone,
        over_budget=Decimal(0)
        if unlimited
        else max(Decimal(0), cost - _money(trip.budget_to)),
        needs_approval=False,
        kappa=None,
        approval_status=ApprovalStatus.NOT_NEEDED,
    )


def build_content(
    data: PlanningInput,
    group: GroupPlan,
    names: Mapping[UUID, str],
    *,
    solo_elapsed_ms: int | None = None,
) -> dict[str, object]:
    """The part of ``PlanRead`` that the solver determines.

    The caller adds ``id``, ``trip_id``, ``version``, ``input_hash``,
    ``plan_hash``, ``created_at`` and ``params``. ``needs_approval`` of the budget
    stays False: the consent to go over ``B_do`` with its price per point is
    backend#53; unpriced places are reported as ``unknown_price`` conflicts.

    Args:
        data: The planning input the plan was computed from.
        group: The result of ``plan_group``.
        names: Display names by profile id.
        solo_elapsed_ms: Override of the solo runs' time (default from ``group``).

    Returns:
        JSON-ready content (``mode="json"`` dump).
    """
    plan = group.plan
    places = {p.id: p for p in data.places}
    people = {p.id: p for p in data.people}
    explain_entries = [
        ExplainEntry(
            place_id=card.place_id,
            profile_id=card.person_id,
            match=card.match,
            effort=card.effort,
            utility=card.utility,
        )
        for pid in plan.place_ids
        for person in sorted(people.values(), key=lambda p: str(p.id))
        for card in [explain(person, places[pid], has_lodging=data.trip.has_lodging)]
    ]
    elapsed = plan.telemetry.elapsed_ms + (
        group.solo_elapsed_ms if solo_elapsed_ms is None else solo_elapsed_ms
    )
    telemetry = PlanTelemetry(
        solver=plan.telemetry.solver,
        steps=plan.telemetry.steps,
        solo_runs=group.solo_runs,
        elapsed_ms=elapsed,
    )
    return {
        "days": [d.model_dump(mode="json") for d in _stops(data, plan, places)],
        "lodging": None,
        "fairness": _fairness(data, group, names, places).model_dump(mode="json"),
        "floors_missed": [
            f.model_dump(mode="json") for f in _floors_missed(group, data, places)
        ],
        "violation": plan.objective.violation,
        "conflicts": [c.model_dump(mode="json") for c in _conflicts(data, group, plan)],
        "explain": [e.model_dump(mode="json") for e in explain_entries],
        "verdicts": None,
        "budget": _budget(data, plan).model_dump(mode="json"),
        "telemetry": telemetry.model_dump(mode="json"),
    }
