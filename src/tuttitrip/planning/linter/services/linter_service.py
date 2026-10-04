"""Check plans."""

from tuttitrip.planning.linter.logic.rules import lint
from tuttitrip.planning.linter.schemas import LintReport, LintRequest


def check_plan(request: LintRequest) -> LintReport:
    """Lint a structured plan.

    Args:
        request: Plan and context.

    Returns:
        The report with every rule.
    """
    return lint(request.plan, request.context)
