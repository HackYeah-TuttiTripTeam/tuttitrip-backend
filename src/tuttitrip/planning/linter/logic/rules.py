"""Lint rules. Each rule takes the request and returns its violations."""

from decimal import Decimal

from tuttitrip.planning.linter.schemas import LintRequest, Violation


def budget_rule(request: LintRequest) -> list[Violation]:
    """Flag plans whose total cost exceeds the budget.

    Args:
        request: Plan and budget.

    Returns:
        One violation if over budget, otherwise none.
    """
    total = sum((item.cost for item in request.items), Decimal(0))
    if total <= request.budget:
        return []
    return [Violation(rule="budget", message=f"Total {total} exceeds {request.budget}")]


RULES = (budget_rule,)


def lint(request: LintRequest) -> list[Violation]:
    """Run every rule.

    Args:
        request: Plan and budget.

    Returns:
        All violations, in rule order.
    """
    return [violation for rule in RULES for violation in rule(request)]
