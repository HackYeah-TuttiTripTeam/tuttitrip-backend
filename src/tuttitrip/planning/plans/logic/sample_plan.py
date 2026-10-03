"""Fixed sample plan: the numbers of ``docs/algorytm.md``, section 7 (STUB).

Pure and deterministic, so the contract can be consumed before the solver
(HackYeah-TuttiTripTeam/tuttitrip-backend#50) exists. Identical input gives
an identical ``plan_hash``.
"""

import datetime as dt
import hashlib
import json
from operator import itemgetter
from uuid import UUID, uuid5

from tuttitrip.accommodation.schemas import RequirementStatus
from tuttitrip.planning.plans.schemas import (
    Budget,
    BudgetZone,
    Conflict,
    Domain,
    DomainScore,
    ExplainEntry,
    Fairness,
    Lodging,
    PersonFairness,
    PlaceKind,
    PlanCreate,
    PlanDay,
    PlanItem,
    PlanParams,
    PlanRead,
    RequirementState,
    Telemetry,
)

PLAN_HASH_LENGTH = 12
DELTA = 0.15  # inflation of an unverified price (E6)
B_FROM, B_TO, B_MAX = 1300.0, 1700.0, 1900.0
CREATED_AT = dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.UTC)
_NS = UUID("6f1f4a3e-5b0a-4a53-9a43-2c7d8e0f1a11")

# (name, u*, u, floor, floor_eff, q per domain in Domain order, own days)
_PEOPLE = (
    ("Ty", 60.3, 51.4, 30.0, 30.0, (78, 72, 90, 60, 85), 3),
    ("Kasia", 62.2, 55.9, 35.0, 35.0, (80, 70, 75, None, 85), 2),
    ("Tomek", 43.8, 40.8, 30.0, 26.3, (50, 65, 70, 40, 85), 2),
    ("Babcia", 64.6, 63.6, 30.0, 30.0, (75, 80, 95, 55, 85), 3),
)

# day -> (name, kind, start, end, transfer_min, base price, verified)
_DAYS = (
    (
        ("Muzeum Gdańska", PlaceKind.ATTRACTION, "10:00", "12:00", 0, 25.0, True),
        ("Restauracja indyjska", PlaceKind.FOOD, "13:00", "14:30", 15, 60.0, True),
    ),
    (
        ("Hevelianum", PlaceKind.ATTRACTION, "10:00", "13:00", 0, 45.0, True),
        ("Bar mleczny", PlaceKind.FOOD, "13:30", "14:30", 15, 30.0, True),
        ("Kawiarnia w ogrodzie", PlaceKind.FOOD, "16:00", "17:00", 20, 35.0, True),
    ),
    (
        ("Park Oliwski", PlaceKind.ATTRACTION, "10:00", "12:00", 0, 0.0, True),
        ("Planszówki", PlaceKind.ATTRACTION, "14:00", "16:00", 20, 20.0, True),
        ("Pizzeria", PlaceKind.FOOD, "18:00", "19:30", 15, 50.0, False),
    ),
)
_NIGHTS, _LODGING_COST = 2, 385.0


def _id(trip_id: UUID, *parts: object) -> UUID:
    return uuid5(_NS, "|".join([str(trip_id), *map(str, parts)]))


def _time(text: str) -> dt.time:
    return dt.time.fromisoformat(text)


def jain_index(values: list[float]) -> float:
    """Jain's fairness index ``(sum x)^2 / (n * sum x^2)``.

    Args:
        values: Non-negative shares, at least one.

    Returns:
        A value in (0, 1]; 1 for a single value or all equal.
    """
    total = sum(values)
    squares = sum(v * v for v in values)
    return 1.0 if squares == 0 else total * total / (len(values) * squares)


def compute_plan_hash(content: object) -> str:
    """Reproducible hash of a plan's content (E5 tie-break).

    Args:
        content: JSON-serializable plan content without ids and timestamps.

    Returns:
        First 12 hex characters of the SHA-256 of the canonical JSON.
    """
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:PLAN_HASH_LENGTH]


def _fairness(trip_id: UUID) -> Fairness:
    rows: list[PersonFairness] = []
    for name, u_star, u, floor, floor_eff, qs, own_days in _PEOPLE:
        domains = [
            DomainScore(domain=d, q=q, not_applicable=q is None)
            for d, q in zip(Domain, qs, strict=True)
        ]
        applicable = [(s.q, s.domain) for s in domains if s.q is not None]
        weakest = min(applicable, key=itemgetter(0))[1]
        rows.append(
            PersonFairness(
                profile_id=_id(trip_id, "profile", name),
                name=name,
                u=u,
                u_star=u_star,
                r=min(1.0, (u + 10) / (u_star + 10)),
                floor=floor,
                floor_eff=floor_eff,
                floor_met=u >= floor_eff,
                domains=domains,
                own_place_days=own_days,
                weakest_domain=weakest,
            )
        )
    shares = [row.r for row in rows]
    return Fairness(
        group_size=len(rows),
        jain=jain_index(shares),
        min_r=min(shares),
        per_person=rows,
    )


def _days(trip_id: UUID) -> list[PlanDay]:
    return [
        PlanDay(
            index=number,
            items=[
                PlanItem(
                    place_id=_id(trip_id, "place", name),
                    name=name,
                    kind=kind,
                    lat=54.35 + 0.01 * position,
                    lon=18.65 + 0.01 * number,
                    start=_time(start),
                    end=_time(end),
                    transfer_min=transfer,
                    cost_per_person=round(base * (1 if verified else 1 + DELTA), 2),
                    price_base=base,
                    price_inflated=round(base * (1 if verified else 1 + DELTA), 2),
                    price_verified=verified,
                    price_source_url=f"https://example.com/{position}"
                    if verified
                    else None,
                    hours_verified=True,
                    google_place_id=None,
                )
                for position, (
                    name,
                    kind,
                    start,
                    end,
                    transfer,
                    base,
                    verified,
                ) in enumerate(day)
            ],
        )
        for number, day in enumerate(_DAYS, start=1)
    ]


def sample_plan(trip_id: UUID, params: PlanCreate | None = None) -> PlanRead:
    """Build the fixed plan for a trip (the section 7 demo group of four).

    Args:
        trip_id: The trip the plan belongs to.
        params: Requested knobs; echoed back. Defaults apply when None.

    Returns:
        The plan; the same arguments always give the same value.
    """
    params = params or PlanCreate()
    days = _days(trip_id)
    fairness = _fairness(trip_id)
    group = fairness.group_size
    per_person = sum(item.cost_per_person for day in days for item in day.items)
    cost = round(per_person * group + _LODGING_COST, 2)
    first = days[0].items[0].place_id
    content = {
        "alpha": params.alpha,
        "preset": params.weight_preset,
        "days": [d.model_dump(mode="json") for d in days],
        "fairness": fairness.model_dump(
            mode="json", exclude={"per_person": {"__all__": {"profile_id"}}}
        ),
        "cost": cost,
    }
    return PlanRead(
        id=_id(trip_id, "plan"),
        trip_id=trip_id,
        version=1,
        input_hash=hashlib.sha256(str(trip_id).encode()).hexdigest(),
        plan_hash=compute_plan_hash(content),
        created_at=CREATED_AT,
        params=PlanParams(alpha=params.alpha, weight_preset=params.weight_preset),
        days=days,
        lodging=Lodging(
            name="Apartament z basenem",
            lat=54.36,
            lon=18.64,
            nights=_NIGHTS,
            cost_total=_LODGING_COST,
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
            Conflict(
                code="pool_only_lodging",
                reason="Tylko ten nocleg spełnia twardy wymóg basenu.",
            )
        ],
        explain=[
            ExplainEntry(
                place_id=first,
                profile_id=row.profile_id,
                match=0.7,
                effort=0.2,
                utility=70.0,
            )
            for row in fairness.per_person
        ],
        verdicts=None,
        budget=Budget(
            cost=cost,
            b_from=B_FROM,
            b_to=B_TO,
            b_max=B_MAX,
            zone=BudgetZone.UP_TO_B_TO,
            over_budget=max(0.0, cost - B_TO),
            needs_approval=False,
            kappa=None,
        ),
        telemetry=Telemetry(solver="stub", steps=0, solo_runs=0, elapsed_ms=0),
    )
