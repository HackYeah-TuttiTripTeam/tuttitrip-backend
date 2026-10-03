"""Check plans."""

from tuttitrip.planning.linter.logic.rules import lint
from tuttitrip.planning.linter.schemas import LintReport, LintRequest


def check_plan(request: LintRequest) -> LintReport:
    """Lint a structured plan.

    Args:
        request: Plan and budget.

    Returns:
        The report with all violations.
    """
    return LintReport(violations=lint(request))
