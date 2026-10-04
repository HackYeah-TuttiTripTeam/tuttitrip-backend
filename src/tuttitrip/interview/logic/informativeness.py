"""Choose the question whose answer changes the plan most (issue #62).

The measuring is the planning domain's (``what_if``: the solver re-run for a few
plausible answers); this module maps questions to what is measured and ranks
them. No language model takes part: the number comes from the solver, so the
interview is led by the algorithm. The card of a question is the fixed map in
``constants.CARD_OF_FIELD``.
"""

from collections.abc import Mapping, Sequence

from tuttitrip.interview import constants
from tuttitrip.interview.schemas import CardKind, NextQuestion, QuestionField
from tuttitrip.planning.schemas import WhatIfField, WhatIfTarget

_MEASURED = {
    QuestionField.DATES: WhatIfField.DATES,
    QuestionField.PEOPLE: WhatIfField.PEOPLE,
    QuestionField.BUDGET: WhatIfField.BUDGET,
    QuestionField.PACE: WhatIfField.PACE,
    QuestionField.IMPORTANCE: WhatIfField.IMPORTANCE,
    QuestionField.REQUIREMENTS: WhatIfField.REQUIREMENTS,
    QuestionField.INTERESTS: WhatIfField.INTERESTS,
}
"""Questions the solver can measure. The destination and the diet cannot: the
former is needed before any plan exists, the latter does not enter the plan."""


def target_of(question: NextQuestion) -> WhatIfTarget | None:
    """What the solver should re-run for this question.

    Args:
        question: A candidate question.

    Returns:
        The probe, or None when its answer cannot be measured.
    """
    measured = _MEASURED.get(question.field)
    if measured is None:
        return None
    return WhatIfTarget(field=measured, person_id=question.person_id)


def card_kind(field: QuestionField) -> CardKind:
    """The card a question is shown on: a fixed map, no model involved.

    Args:
        field: What the question is about.

    Returns:
        The card kind.
    """
    return constants.CARD_OF_FIELD[field]


def pick(
    questions: Sequence[NextQuestion], scores: Mapping[WhatIfTarget, float]
) -> NextQuestion | None:
    """The question with the highest score; ties go to the field name.

    A question that cannot be measured scores zero, so it comes after every
    question that changes the plan at all.

    Args:
        questions: The candidates (``next_question.open_questions``).
        scores: The measured change per probe.

    Returns:
        The best question with its ``impact``, or None for no candidates.
    """
    ranked = sorted(
        (
            (
                scores.get(target, 0.0) if (target := target_of(q)) else 0.0,
                q,
            )
            for q in questions
        ),
        key=lambda item: (-item[0], item[1].field.value, str(item[1].person_id)),
    )
    if not ranked:
        return None
    score, best = ranked[0]
    return best.model_copy(update={"impact": score})
