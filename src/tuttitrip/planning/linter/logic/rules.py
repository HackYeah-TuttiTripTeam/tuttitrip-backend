"""Rule registry and the linter entry point.

A rule is one module with ``RULE`` (code, explicit weight, pure check). To add
one, write the module and list it in ``RULES``; the order here is the order of
the report.
"""

from tuttitrip.planning.linter.logic import (
    budget,
    closed_day,
    opening_hours,
    transfer,
    unknown_place,
)
from tuttitrip.planning.linter.logic.rule import Rule
from tuttitrip.planning.linter.schemas import (
    LintContext,
    LintPlan,
    LintReport,
    RuleResult,
    Severity,
)
from tuttitrip.planning.plans.logic.hashing import compute_plan_hash

RULES: tuple[Rule, ...] = (
    closed_day.RULE,
    opening_hours.RULE,
    transfer.RULE,
    budget.RULE,
    unknown_place.RULE,
)


def lint(plan: LintPlan, context: LintContext) -> LintReport:
    """Run every rule; each appears in the result, also with zero violations.

    Args:
        plan: The plan.
        context: Places, zone and budget.

    Returns:
        Results in registry order, the weighted score and a digest of them.
    """
    results: list[RuleResult] = []
    for rule in RULES:
        findings = rule.check(plan, context)
        violations = [f for f in findings if f.severity is Severity.VIOLATION]
        results.append(
            RuleResult(
                rule=rule.code,
                weight=rule.weight,
                count=len(violations),
                violations=violations,
                warnings=[f for f in findings if f.severity is Severity.WARNING],
            )
        )
    digest = compute_plan_hash([r.model_dump(mode="json") for r in results])
    return LintReport(
        results=results,
        count=sum(r.count for r in results),
        score=sum(r.weight * r.count for r in results),
        digest=digest,
    )
