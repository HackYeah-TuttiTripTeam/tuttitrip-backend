"""Which question to ask next: the missing data that changes the plan most.

Pure and deterministic. The order is an explicit table (``constants``):
destination and dates first (nothing can be planned without them), then the
group, then the budget, then what shapes the pace and the choice of places:
the slowest person's tempo, the importance pool, access requirements,
interests and diet. The language model only words the question.
"""

from collections.abc import Collection

from tuttitrip.interview import constants
from tuttitrip.interview.schemas import (
    ConstraintKind,
    FieldRef,
    KnowledgeField,
    KnowledgeRead,
    NextQuestion,
    QuestionField,
    QuestionKey,
    ValueSource,
)
from tuttitrip.places.schemas import DietTag, PlaceTag
from tuttitrip.profiles.preferences.schemas import ImportanceDomain
from tuttitrip.profiles.schemas import ProfileRead

_MISSING_KNOWLEDGE = {
    QuestionField.DESTINATION: FieldRef(field=KnowledgeField.DESTINATION),
    QuestionField.DATES: FieldRef(field=KnowledgeField.DATES),
    QuestionField.PEOPLE: FieldRef(field=KnowledgeField.PEOPLE),
    QuestionField.BUDGET: FieldRef(field=KnowledgeField.BUDGET),
}


def _options(field: QuestionField) -> list[str]:
    match field:
        case QuestionField.IMPORTANCE:
            return [d.value for d in ImportanceDomain]
        case QuestionField.REQUIREMENTS:
            return [k.value for k in ConstraintKind]
        case QuestionField.INTERESTS:
            return [t.value for t in PlaceTag]
        case QuestionField.DIET:
            return [d.value for d in DietTag]
        case _:
            return []


def _slowest_first(people: list[ProfileRead]) -> list[ProfileRead]:
    return sorted(people, key=lambda p: (p.segment_km, p.daily_km, p.active_min))


def _host_filled(knowledge: KnowledgeRead) -> set[object]:
    return {
        s.profile_id
        for s in knowledge.sources
        if s.field is KnowledgeField.PREFERENCES and s.source is ValueSource.HOST
    }


def _question(field: QuestionField, person: ProfileRead | None) -> NextQuestion:
    return NextQuestion(
        field=field,
        card_kind=constants.CARD_OF_FIELD[field],
        person_id=person.id if person else None,
        options=_options(field),
    )


def open_questions(
    knowledge: KnowledgeRead, asked: Collection[QuestionKey] = frozenset()
) -> list[NextQuestion]:
    """The next question of every field that still has one, in the fixed order.

    A trip-level field is asked until it is filled, including a value the host
    set or corrected. A question about a person is asked once, and never for a
    person whose preferences the host filled in themselves. Of a person field
    the first person who has not been asked is the candidate.

    Args:
        knowledge: The "What we already know" view.
        asked: Questions the assistant already put on screen.

    Returns:
        At most one question per field: the group fields in table order, then
        the person fields in table order.
    """
    missing = set(knowledge.missing)
    found = [
        _question(field, None)
        for field in constants.GROUP_FIELDS
        if _MISSING_KNOWLEDGE[field] in missing
    ]
    by_host = _host_filled(knowledge)
    people = [p for p in _slowest_first(knowledge.people) if p.id not in by_host]
    for field in constants.PERSON_FIELDS:
        targets = people[:1] if field in constants.SLOWEST_ONLY else people
        person = next(
            (
                p
                for p in targets
                if QuestionKey(field=field, person_id=p.id) not in asked
            ),
            None,
        )
        if person is not None:
            found.append(_question(field, person))
    return found


def next_question(
    knowledge: KnowledgeRead, asked: Collection[QuestionKey] = frozenset()
) -> NextQuestion | None:
    """Pick the next question by the fixed table, or none when nothing is left.

    This is the order used when the impact on the plan cannot be measured
    (``informativeness.pick`` otherwise chooses among ``open_questions``).

    Args:
        knowledge: The "What we already know" view.
        asked: Questions the assistant already put on screen.

    Returns:
        The question with the card to show, or ``None``.
    """
    return next(iter(open_questions(knowledge, asked)), None)


def member_question(
    knowledge: KnowledgeRead, asked: Collection[QuestionKey] = frozenset()
) -> NextQuestion | None:
    """The next question for a member about themselves, or none when done.

    The member is asked about their interests, limits, diet and priorities, in
    that order, once each. Never about the trip's budget, dates or other people.

    Args:
        knowledge: The member's panel (only themselves).
        asked: Questions the assistant already put on screen.

    Returns:
        The question with the card to show, or ``None``.
    """
    me = next(iter(knowledge.people), None)
    if me is None:
        return None
    return next(
        (
            _question(field, me)
            for field in constants.MEMBER_FIELDS
            if QuestionKey(field=field, person_id=me.id) not in asked
        ),
        None,
    )
