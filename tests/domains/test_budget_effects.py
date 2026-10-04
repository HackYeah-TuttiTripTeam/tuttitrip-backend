"""The numbers of a budget consent log entry: P_flex minus P_strict."""

import uuid
from decimal import Decimal

from tuttitrip.planning.budget_approvals.logic.effects import consent_effects
from tuttitrip.planning.overrides.schemas import BudgetConsent, BudgetOutcome
from tuttitrip.planning.plans.logic.sample_plan import Scenario, sample_plan

TRIP = uuid.UUID("00000000-0000-4000-8000-000000000042")


def consent() -> BudgetConsent:
    return BudgetConsent(
        approval_id=uuid.uuid4(),
        outcome=BudgetOutcome.APPROVED,
        currency="PLN",
        over_budget=Decimal("98.00"),
        kappa=Decimal("18.70"),
        gain_profile_id=None,
        gain_points=5.2,
    )


def test_the_effects_are_flex_minus_strict_with_the_consent_attached() -> None:
    flex = sample_plan(TRIP, scenario=Scenario.APPROVAL)
    strict = sample_plan(TRIP, scenario=Scenario.GROUP)
    given = consent()
    effects = consent_effects(flex, strict, given)
    assert effects.budget == given
    assert effects.d_cost == flex.budget.cost - strict.budget.cost
    assert effects.d_min_r == flex.fairness.min_r - strict.fairness.min_r
    assert effects.d_jain == flex.fairness.jain - strict.fairness.jain
    assert len(effects.d_r) == len(flex.fairness.per_person)


def test_the_same_plan_changes_nothing() -> None:
    plan = sample_plan(TRIP)
    effects = consent_effects(plan, plan, consent())
    assert effects.d_cost == 0
    assert effects.d_minutes == 0
    assert effects.d_min_r == 0
    assert all(d.d_r == 0 for d in effects.d_r)
