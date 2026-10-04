"""Rule ``budget``: the plan costs at most ``B_max`` (E0, E6)."""

from decimal import Decimal

from tuttitrip.planning.linter.logic.rule import Rule, stops_by_day
from tuttitrip.planning.linter.schemas import Finding, LintContext, LintPlan, Severity

CODE = "budget"
UNVERIFIED_PRICE_DELTA = Decimal("0.15")
"""E6: an unverified price counts ``1 + delta`` times."""


def check(plan: LintPlan, context: LintContext) -> list[Finding]:
    """Flag a plan whose cost exceeds ``B_max``.

    Amounts count as stated in the plan; only items marked unverified are
    inflated by delta, like the solver does.

    Args:
        plan: The plan.
        context: Budget and flex.

    Returns:
        One violation when over ``B_max``, otherwise none.
    """
    total = sum(
        (
            stop.item.cost
            * (1 if stop.item.price_verified else 1 + UNVERIFIED_PRICE_DELTA)
            for _, stops in stops_by_day(plan, context)
            for stop in stops
        ),
        Decimal(0),
    )
    if total <= context.b_max:
        return []
    return [
        Finding(
            rule=CODE,
            severity=Severity.VIOLATION,
            message=f"Cost {total} exceeds B_max {context.b_max}",
        )
    ]


RULE = Rule(CODE, 4, check)
