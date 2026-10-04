"""The daily budget and the proposal after an overrun (backend#89).

``get_days`` is cheap and read-only. ``propose`` runs the solver (in a worker
thread) and stores the result as an alternative of the latest plan version: the
plan itself changes only when the host approves. The same expenses and data give
the stored proposal back, so asking twice costs one computation.
"""

import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID

import anyio.to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.expenses.services import expense_service
from tuttitrip.planning.budget.logic.daily_budget import (
    DayBudget,
    budget_left,
    day_budgets,
    last_overrun,
    rest_of_trip,
)
from tuttitrip.planning.budget.logic.proposal import RestProposal, propose_rest
from tuttitrip.planning.budget.schemas import (
    BudgetDaysRead,
    BudgetProposalRead,
    DayBudgetRead,
    ProposalOutcome,
    ProposalSkip,
)
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.logic.solver import Assignment
from tuttitrip.planning.plans import db
from tuttitrip.planning.plans.logic.input_builder import ALGORITHM_VERSION, input_hash
from tuttitrip.planning.plans.logic.read_model import build_content
from tuttitrip.planning.plans.models import PlanVersion
from tuttitrip.planning.plans.schemas import ApprovalStatus, WeightPreset
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import PlanNotFoundError
from tuttitrip.planning.schemas import PlanningInput
from tuttitrip.planning.services.solver_service import configured_solver
from tuttitrip.trips.schemas import TripMembership, TripRead
from tuttitrip.trips.services import trip_service

PROPOSAL_KEY = "proposal"
"""Key of ``PlanVersion.params`` that marks a stored budget proposal."""


class BudgetInputError(Exception):
    """The trip cannot be budgeted: no dates or budget, or the plan is outdated."""


def _days(trip: TripRead) -> list[date]:
    if trip.start_date is None or trip.end_date is None:
        msg = "The trip needs dates for a daily budget"
        raise BudgetInputError(msg)
    count = (trip.end_date - trip.start_date).days + 1
    return [trip.start_date + timedelta(days=i) for i in range(count)]


async def _budgets(
    session: AsyncSession, membership: TripMembership, trip: TripRead
) -> tuple[list[DayBudget], Decimal, Decimal]:
    # The days with their spending, and the trip's B_od and B_do.
    low, high = trip.budget_total_min, trip.budget_total_max
    if low is None or high is None:
        msg = "The trip needs a budget for a daily budget"
        raise BudgetInputError(msg)
    totals = await expense_service.day_totals(session, membership)
    budgets = day_budgets(
        _days(trip),
        total_from=low,
        total_to=high,
        flex_pct=trip.budget_flex_pct,
        day_from=trip.budget_day_min,
        day_to=trip.budget_day_max,
        totals=totals,
    )
    return budgets, low, high


def _money(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"))


def _day_read(b: DayBudget) -> DayBudgetRead:
    return DayBudgetRead(
        index=b.index,
        date=b.day,
        budget_from=_money(b.budget_from),
        budget_to=_money(b.budget_to),
        budget_max=_money(b.budget_max),
        spent=_money(b.spent),
        outside_plan=_money(b.outside_plan),
        remaining=_money(b.remaining),
        remaining_max=_money(b.remaining_max),
        over_budget=b.over_budget,
        over_max=b.over_max,
    )


def _proposal_read(row: PlanVersion) -> BudgetProposalRead:
    info = row.params[PROPOSAL_KEY]
    budget = row.result["budget"]
    min_r = row.result["fairness"]["min_r"]
    previous_r = info["previous_min_r"]
    cost = Decimal(str(budget["cost"]))
    previous_cost = Decimal(info["previous_cost"])
    return BudgetProposalRead(
        plan_id=row.id,
        version=row.version,
        from_day=info["from_day"],
        from_date=date.fromisoformat(info["from_date"]),
        overrun_days=info["overrun_days"],
        budget_to=_money(Decimal(info["budget_to"])),
        cost=_money(cost),
        previous_cost=_money(previous_cost),
        cost_delta=_money(cost - previous_cost),
        min_r=min_r,
        previous_min_r=previous_r,
        min_r_delta=None if previous_r is None else min_r - previous_r,
        needs_approval=budget["needs_approval"],
        kappa=None if budget["kappa"] is None else Decimal(str(budget["kappa"])),
        approval_status=ApprovalStatus(budget["approval_status"]),
    )


async def get_days(session: AsyncSession, membership: TripMembership) -> BudgetDaysRead:
    """The budget of every day, what was spent and the stored proposal.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.

    Returns:
        The days and the proposal for the latest plan, if there is one.

    Raises:
        BudgetInputError: The trip has no dates or no budget.
    """
    trip = await trip_service.get_trip(session, membership)
    budgets, _, _ = await _budgets(session, membership, trip)
    latest = await db.select_latest(session, membership.trip_id)
    row = (
        None
        if latest is None
        else await db.select_proposal(session, membership.trip_id, latest.id)
    )
    return BudgetDaysRead(
        currency=trip.currency or "",
        propose_cheaper_alternatives=trip.propose_cheaper_alternatives,
        total_spent=_money(sum((b.spent for b in budgets), Decimal(0))),
        days=[_day_read(b) for b in budgets],
        proposal=None if row is None else _proposal_read(row),
    )


def _place_ids(day: dict[str, Any]) -> tuple[UUID, ...]:
    return tuple(sorted((UUID(s["place_id"]) for s in day["items"]), key=str))


def _compute(
    sub: PlanningInput,
    alpha: float,
    names: dict[UUID, str],
    assignment: Assignment,
    first_day: int,
) -> tuple[RestProposal, dict[str, object]] | None:
    # Runs in a worker thread: the solver and the comparison with the plan's days.
    started = time.perf_counter()
    found = propose_rest(
        sub,
        DEFAULT_PARAMS,
        alpha=alpha,
        assignment=assignment,
        solver=configured_solver().solver,
    )
    if found is None:
        return None
    content = build_content(
        sub,
        found.decision.chosen,
        names,
        decision=found.decision,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        first_day=first_day,
    )
    return found, content


@dataclass(frozen=True, slots=True)
class _Rest:
    """Everything a proposal is computed from."""

    latest_id: UUID
    """The plan version the proposal is an alternative of."""
    version: int
    """Its version number (kept as plain data: the rollback expires the row)."""
    over: DayBudget
    """The last day that went over."""
    budgets: list[DayBudget]
    sub: PlanningInput
    """The rest of the trip with the budget that is left."""
    alpha: float
    names: dict[UUID, str]
    assignment: Assignment
    """The plan's own places in the remaining days."""
    digest: str
    left_to: Decimal


def _visited(days: list[dict[str, Any]]) -> frozenset[UUID]:
    return frozenset(UUID(stop["place_id"]) for day in days for stop in day["items"])


def _skip(trip: TripRead, over: DayBudget | None, days: int) -> ProposalSkip | None:
    if not trip.propose_cheaper_alternatives:
        return ProposalSkip.DISABLED
    if over is None:
        return ProposalSkip.NO_OVERRUN
    return ProposalSkip.NO_DAYS_LEFT if over.index == days else None


async def _prepare(
    session: AsyncSession, membership: TripMembership, trip: TripRead
) -> _Rest | ProposalSkip:
    budgets, total_from, total_to = await _budgets(session, membership, trip)
    over = last_overrun(budgets)
    skipped = _skip(trip, over, len(budgets))
    if skipped is not None or over is None:
        return skipped or ProposalSkip.NO_OVERRUN
    latest = await db.select_latest(session, membership.trip_id)
    if latest is None:
        raise PlanNotFoundError(str(membership.trip_id))
    planned = latest.result["days"]
    if len(planned) != len(budgets):
        msg = "The plan does not match the trip's days: generate a new plan"
        raise BudgetInputError(msg)
    planning, names, alpha = await plan_service.gather_input(session, membership)
    left_from, left_to = budget_left(
        budgets, over.index, total_from=total_from, total_to=total_to
    )
    sub = rest_of_trip(
        planning,
        after=over.index,
        visited=_visited(planned[: over.index]),
        budget_from=left_from,
        budget_to=left_to,
    )
    digest = input_hash(
        sub, alpha, WeightPreset.DEFAULT.value, DEFAULT_PARAMS, configured_solver().tag
    )
    return _Rest(
        latest.id,
        latest.version,
        over,
        budgets,
        sub,
        alpha,
        names,
        tuple(_place_ids(day) for day in planned[over.index :]),
        digest,
        left_to,
    )


async def _store(
    session: AsyncSession,
    membership: TripMembership,
    rest: _Rest,
    found: RestProposal,
    content: dict[str, object],
) -> ProposalOutcome:
    await db.lock_trip_plans(session, membership.trip_id)
    again = await db.select_proposal(
        session, membership.trip_id, rest.latest_id, rest.digest
    )
    if again is not None:  # a parallel request stored it first
        return ProposalOutcome(created=False, proposal=_proposal_read(again))
    info: dict[str, object] = {
        "from_day": rest.over.index + 1,
        "from_date": rest.sub.trip.days[0].isoformat(),
        "overrun_days": [b.index for b in rest.budgets if b.over_budget],
        "budget_to": str(rest.left_to),
        "previous_cost": str(found.previous.cost),
        "previous_min_r": found.previous.min_r,
    }
    row = PlanVersion(
        id=uuid.uuid4(),
        trip_id=membership.trip_id,
        version=rest.version,
        input_hash=rest.digest,
        plan_hash=found.decision.chosen.plan.plan_hash,
        params={
            "alpha": rest.alpha,
            "weight_preset": WeightPreset.DEFAULT.value,
            "algorithm_version": ALGORITHM_VERSION,
            "algorithm": asdict(DEFAULT_PARAMS),
            PROPOSAL_KEY: info,
        },
        result=content,
        created_by_sub=membership.sub,
        alternative_of=rest.latest_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ProposalOutcome(created=True, proposal=_proposal_read(row))


async def propose(session: AsyncSession, membership: TripMembership) -> ProposalOutcome:
    """Plan the days after the last overrun with the budget that is left.

    Args:
        session: Open session.
        membership: The caller's membership (co-host or host).

    Returns:
        The stored proposal (new or existing), or why there is none.

    Raises:
        BudgetInputError: No dates or budget, or the plan no longer matches the trip.
        PlanNotFoundError: The trip has no plan yet.
    """
    trip = await trip_service.get_trip(session, membership)
    rest = await _prepare(session, membership, trip)
    if isinstance(rest, ProposalSkip):
        return ProposalOutcome(created=False, skipped=rest)
    existing = await db.select_proposal(
        session, membership.trip_id, rest.latest_id, rest.digest
    )
    if existing is not None:
        return ProposalOutcome(created=False, proposal=_proposal_read(existing))
    await session.rollback()  # do not hold a transaction while computing
    computed = await anyio.to_thread.run_sync(
        partial(
            _compute,
            rest.sub,
            rest.alpha,
            rest.names,
            rest.assignment,
            rest.over.index + 1,
        )
    )
    if computed is None:
        return ProposalOutcome(created=False, skipped=ProposalSkip.NO_SAVING)
    return await _store(session, membership, rest, *computed)
