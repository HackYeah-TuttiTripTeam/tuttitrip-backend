"""Fixed sample plans: the numbers of ``docs/algorytm.md``, section 7 (STUB).

Pure and deterministic, so the contract can be consumed before the solver
(HackYeah-TuttiTripTeam/tuttitrip-backend#50) exists. Identical input gives
an identical ``plan_hash``. Holds fixtures only; metrics and hashing live in
``metrics.py`` and ``hashing.py``.
"""

import datetime as dt
import hashlib
from decimal import Decimal
from enum import StrEnum
from operator import itemgetter
from typing import NamedTuple
from uuid import UUID, uuid5

from tuttitrip.accommodation.schemas import RequirementStatus
from tuttitrip.planning.plans.logic.hashing import compute_plan_hash
from tuttitrip.planning.plans.logic.metrics import jain_index
from tuttitrip.planning.plans.schemas import (
    ApprovalStatus,
    BudgetZone,
    ConflictCode,
    Currency,
    ExplainEntry,
    PersonFairness,
    PlaceKind,
    PlanBudget,
    PlanConflict,
    PlanCreate,
    PlanDay,
    PlanDomainCode,
    PlanDomainScore,
    PlanFairness,
    PlanLodging,
    PlanParams,
    PlanRead,
    PlanStop,
    PlanTelemetry,
    RequirementState,
    StopTransfer,
    TransferMode,
)

DELTA = Decimal("0.15")  # inflation of an unverified price (E6)
CENT = Decimal("0.01")
CREATED_AT = dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.UTC)
VERIFIED_AT = dt.datetime(2026, 9, 20, 9, 0, tzinfo=dt.UTC)
_NS = UUID("6f1f4a3e-5b0a-4a53-9a43-2c7d8e0f1a11")


class Scenario(StrEnum):
    """Which sample to build."""

    GROUP = "group"
    SOLO = "solo"
    APPROVAL = "approval"


class _Person(NamedTuple):
    name: str
    u_star: float
    u: float
    floor: float
    floor_eff: float
    q: tuple[int | None, ...]  # in PlanDomainCode order
    own_days: int


class _Stop(NamedTuple):
    name: str
    kind: PlaceKind
    start: str
    end: str
    transfer_min: int
    base: str
    verified: bool = True


_PEOPLE = (
    _Person("Ty", 60.3, 51.4, 30.0, 30.0, (78, 72, 90, 60, 85), 3),
    _Person("Kasia", 62.2, 55.9, 35.0, 35.0, (80, 70, 75, None, 85), 2),
    _Person("Tomek", 43.8, 40.8, 30.0, 26.3, (50, 65, 70, 40, 85), 2),
    _Person("Babcia", 64.6, 63.6, 30.0, 30.0, (75, 80, 95, 55, 85), 3),
)
_SOLO = _Person("Ty", 82.1, 82.1, 0.0, 0.0, (85, 72, 78, 60, 85), 2)

_DAYS = (
    (
        _Stop("Muzeum Gdańska", PlaceKind.ATTRACTION, "10:00", "12:00", 0, "25"),
        _Stop("Restauracja indyjska", PlaceKind.FOOD, "13:00", "14:30", 15, "60"),
    ),
    (
        _Stop("Hevelianum", PlaceKind.ATTRACTION, "10:00", "13:00", 0, "45"),
        _Stop("Bar mleczny", PlaceKind.FOOD, "13:30", "14:30", 15, "30"),
        _Stop("Kawiarnia w ogrodzie", PlaceKind.FOOD, "16:00", "17:00", 20, "35"),
    ),
    (
        _Stop("Park Oliwski", PlaceKind.ATTRACTION, "10:00", "12:00", 0, "0"),
        _Stop("Planszówki", PlaceKind.ATTRACTION, "14:00", "16:00", 20, "20"),
        _Stop("Pizzeria", PlaceKind.FOOD, "18:00", "19:30", 15, "50", verified=False),
    ),
)

# scenario -> (days, nights, lodging cost, B_od, B_do, B_max)
_BUDGETS = {
    Scenario.GROUP: (3, 2, "385", "1300", "1700", "1900"),
    Scenario.SOLO: (2, 1, "130", "500", "800", "900"),
    Scenario.APPROVAL: (3, 2, "108", "900", "1100", "1300"),
}


def _id(trip_id: UUID, *parts: object) -> UUID:
    return uuid5(_NS, "|".join([str(trip_id), *map(str, parts)]))


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _fairness(trip_id: UUID, people: tuple[_Person, ...]) -> PlanFairness:
    rows: list[PersonFairness] = []
    for person in people:
        name, u_star, u = person.name, person.u_star, person.u
        domains = [
            PlanDomainScore(domain=d, q=q, not_applicable=q is None)
            for d, q in zip(PlanDomainCode, person.q, strict=True)
        ]
        applicable = [(s.q, s.domain) for s in domains if s.q is not None]
        rows.append(
            PersonFairness(
                profile_id=_id(trip_id, "profile", name),
                name=name,
                u=u,
                u_star=u_star,
                r=min(1.0, (u + 10) / (u_star + 10)),
                floor=person.floor,
                floor_eff=person.floor_eff,
                floor_met=u >= person.floor_eff,
                domains=domains,
                own_place_days=person.own_days,
                weakest_domain=min(applicable, key=itemgetter(0))[1],
            )
        )
    shares = [row.r for row in rows]
    return PlanFairness(
        group_size=len(rows),
        jain=jain_index(shares),
        min_r=min(shares),
        per_person=rows,
    )


def _stop(trip_id: UUID, number: int, position: int, spec: _Stop) -> PlanStop:
    name, kind, verified = spec.name, spec.kind, spec.verified
    base = Decimal(spec.base)
    inflated = _cents(base if verified else base * (1 + DELTA))
    stamp = VERIFIED_AT if verified else None
    return PlanStop(
        place_id=_id(trip_id, "place", name),
        name=name,
        kind=kind,
        lat=54.35 + 0.01 * position,
        lon=18.65 + 0.01 * number,
        start=dt.time.fromisoformat(spec.start),
        end=dt.time.fromisoformat(spec.end),
        transfer=StopTransfer(
            minutes=spec.transfer_min, mode=TransferMode.WALK, cost=None
        )
        if position
        else None,
        cost_per_person=inflated,
        price_base=_cents(base),
        price_inflated=inflated,
        price_verified=verified,
        price_source_url=f"https://tickets.invalid/{position}" if verified else None,
        price_verified_at=stamp,
        hours_verified=True,
        hours_source_url=f"https://hours.invalid/{position}",
        hours_verified_at=VERIFIED_AT,
    )


def _budget(trip_id: UUID, scenario: Scenario, cost: Decimal) -> PlanBudget:
    _, _, _, b_from, b_to, b_max = _BUDGETS[scenario]
    approval = scenario is Scenario.APPROVAL
    over = max(Decimal(0), cost - Decimal(b_to))
    tomek = _id(trip_id, "profile", "Tomek")
    if cost < Decimal(b_from):
        zone = BudgetZone.BELOW_B_FROM
    elif cost <= Decimal(b_to):
        zone = BudgetZone.UP_TO_B_TO
    else:
        zone = BudgetZone.IN_MARGIN
    return PlanBudget(
        currency=Currency.PLN,
        cost=cost,
        b_from=Decimal(b_from),
        b_to=Decimal(b_to),
        b_max=Decimal(b_max),
        zone=zone,
        over_budget=over,
        needs_approval=approval,
        kappa=Decimal("18.70") if approval else None,
        gain_profile_id=tomek if approval else None,
        gain_points=5.0 if approval else None,
        strict_plan_id=_id(trip_id, "plan", "strict") if approval else None,
        strict_cost=_cents(cost - Decimal("18.7") * 5) if approval else None,
        approval_status=ApprovalStatus.PENDING
        if approval
        else ApprovalStatus.NOT_NEEDED,
    )


def sample_plan(
    trip_id: UUID,
    params: PlanCreate | None = None,
    scenario: Scenario = Scenario.GROUP,
) -> PlanRead:
    """Build the fixed plan for a trip.

    Args:
        trip_id: The trip the plan belongs to.
        params: Requested knobs; echoed back. Defaults apply when None.
        scenario: ``group`` (section 7 demo of four), ``solo`` (n = 1) or
            ``approval`` (over ``B_do``, needs approval).

    Returns:
        The plan; the same arguments always give the same value.
    """
    params = params or PlanCreate()
    day_count, nights, lodging_cost, *_ = _BUDGETS[scenario]
    people = (_SOLO,) if scenario is Scenario.SOLO else _PEOPLE
    days = [
        PlanDay(
            index=number,
            items=[_stop(trip_id, number, i, spec) for i, spec in enumerate(day)],
        )
        for number, day in enumerate(_DAYS[:day_count], start=1)
    ]
    fairness = _fairness(trip_id, people)
    per_person = sum(
        (s.cost_per_person or Decimal(0) for d in days for s in d.items), Decimal(0)
    )
    cost = _cents(per_person * fairness.group_size + Decimal(lodging_cost))
    budget = _budget(trip_id, scenario, cost)
    content = {
        "scenario": scenario.value,
        "alpha": params.alpha,
        "preset": params.weight_preset.value,
        "days": [d.model_dump(mode="json") for d in days],
        "fairness": fairness.model_dump(
            mode="json", exclude={"per_person": {"__all__": {"profile_id"}}}
        ),
        "cost": str(cost),
    }
    return PlanRead(
        id=_id(trip_id, "plan"),
        trip_id=trip_id,
        version=1,
        input_hash=hashlib.sha256(f"{trip_id}|{scenario.value}".encode()).hexdigest(),
        plan_hash=compute_plan_hash(content),
        created_at=CREATED_AT,
        params=PlanParams(alpha=params.alpha, weight_preset=params.weight_preset),
        days=days,
        lodging=PlanLodging(
            name="Apartament z basenem",
            lat=54.36,
            lon=18.64,
            nights=nights,
            cost_total=_cents(Decimal(lodging_cost)),
            s_h=0.9,
            requirements=[
                RequirementState(
                    feature="pool", hard=True, status=RequirementStatus.MET
                ),
                RequirementState(
                    feature="parking", hard=False, status=RequirementStatus.UNCONFIRMED
                ),
            ],
        ),
        fairness=fairness,
        floors_missed=[],
        violation=0.0,
        conflicts=[
            PlanConflict(
                reason_code=ConflictCode.LODGING_HARD_REQUIREMENT,
                params={"feature": "pool"},
            )
        ]
        if fairness.group_size > 1
        else [],
        explain=[
            ExplainEntry(
                place_id=days[0].items[0].place_id,
                profile_id=row.profile_id,
                match=0.7,
                effort=0.2,
                utility=70.0,
            )
            for row in fairness.per_person
        ],
        verdicts=None,
        budget=budget,
        telemetry=PlanTelemetry(solver="stub", steps=0, solo_runs=0, elapsed_ms=0),
    )
