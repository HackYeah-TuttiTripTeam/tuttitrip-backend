"""What the group is shown: Jain's index, ``min r`` and the violation report.

``r_i = min(1, (u_i + 10) / (u*_i + 10))`` ("x% of your maximum", E4) is
computed by the caller; Jain's index and ``min r`` are measured on it
(sections 7 and 10). For ``n = 1`` Jain is always 1, and the UI shows the domain
chart instead (section 4).
"""

import math
from collections.abc import Sequence

from tuttitrip.planning.fairness.logic.objective import GroupObjective
from tuttitrip.planning.fairness.schemas import Conflict, ConflictCode, FairnessReport


def jain(r: Sequence[float]) -> float:
    """Jain's fairness index ``(sum r)^2 / (n * sum r^2)``.

    Args:
        r: Relative satisfaction of everybody, each in 0 to 1.

    Returns:
        1 when everybody is equally satisfied, down to ``1/n``; 1 for no data
        or all zeros.
    """
    squares = math.fsum(x * x for x in r)
    if not r or squares == 0:
        return 1.0
    return math.fsum(r) ** 2 / (len(r) * squares)


def min_r(r: Sequence[float]) -> float:
    """The worst relative satisfaction.

    Args:
        r: Relative satisfaction of everybody.

    Returns:
        The smallest value, 1 for no data.
    """
    return min(r, default=1.0)


def build_report(objective: GroupObjective) -> FairnessReport:
    """``floors_missed``, ``violation`` and ``conflicts`` of a plan.

    Args:
        objective: ``J`` with the violations per person.

    Returns:
        The report; a person with no violation does not appear.
    """
    floors: list[Conflict] = []
    own: list[Conflict] = []
    tags: list[Conflict] = []
    for item in objective.violations:
        if item.floor_term > 0:
            floors.append(
                Conflict(
                    person_id=item.person_id,
                    code=ConflictCode.FLOOR,
                    missing=item.floor_term,
                )
            )
        if item.own_days_missing:
            own.append(
                Conflict(
                    person_id=item.person_id,
                    code=ConflictCode.OWN_PLACE,
                    missing=item.own_days_missing,
                )
            )
        tags.extend(
            Conflict(
                person_id=item.person_id,
                code=ConflictCode.TAG_MINIMUM,
                tag=t.tag.tag,
                missing=t.required - t.have,
            )
            for t in item.tags
            if t.have < t.required
        )
    return FairnessReport(
        floors_missed=[c.person_id for c in floors],
        violation=objective.violation,
        conflicts=[*floors, *own, *tags],
    )
