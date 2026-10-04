"""What is filled, what is missing and who set each value.

Pure: it gets the data the ``trips`` and ``profiles`` services returned. Who set
a value is not stored in those domains. The interview remembers a digest of
every value the assistant wrote; a stored value whose digest differs was
changed by the host since.
"""

import hashlib
import json
from collections.abc import Mapping

from pydantic import BaseModel

from tuttitrip.interview.schemas import (
    FieldRef,
    FieldSource,
    KnowledgeField,
    ValueSource,
)
from tuttitrip.profiles.preferences.schemas import PreferencesRead
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.trips.schemas import TripRead

MIN_PEOPLE = 2
"""A group needs at least two people; one profile is only the host."""

_BUDGET_FIELDS = {
    "currency",
    "budget_total_min",
    "budget_total_max",
    "budget_day_min",
    "budget_day_max",
    "budget_flex_pct",
}
_PREFERENCE_VOLATILE = {"updated_by_sub", "updated_at", "filled"}


class Value(BaseModel):
    """One value of the panel: is it filled and its digest."""

    filled: bool
    digest: str


def _digest(data: object) -> str:
    text = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def values(
    trip: TripRead,
    people: list[ProfileRead],
    preferences: list[PreferencesRead],
) -> dict[FieldRef, Value]:
    """List every value the panel tracks.

    Args:
        trip: The trip, from ``trip_service``.
        people: Profiles of the trip.
        preferences: Preferences of the same people.

    Returns:
        One entry per trip field, per person and per person's preferences.
    """
    budget = trip.model_dump(mode="json", include=_BUDGET_FIELDS)
    has_budget = trip.budget_total_min is not None or trip.budget_day_min is not None
    dates = [trip.start_date, trip.end_date]
    out = {
        FieldRef(field=KnowledgeField.DESTINATION): Value(
            filled=trip.destination is not None, digest=_digest(trip.destination)
        ),
        FieldRef(field=KnowledgeField.DATES): Value(
            filled=trip.start_date is not None, digest=_digest(dates)
        ),
        FieldRef(field=KnowledgeField.BUDGET): Value(
            filled=has_budget, digest=_digest(budget)
        ),
    }
    for person in people:
        ref = FieldRef(field=KnowledgeField.PEOPLE, profile_id=person.id)
        out[ref] = Value(filled=True, digest=_digest(person.model_dump(mode="json")))
    for prefs in preferences:
        ref = FieldRef(field=KnowledgeField.PREFERENCES, profile_id=prefs.profile_id)
        data = prefs.model_dump(mode="json", exclude=_PREFERENCE_VOLATILE)
        out[ref] = Value(filled=prefs.filled, digest=_digest(data))
    return out


def missing(current: Mapping[FieldRef, Value]) -> list[FieldRef]:
    """List what the assistant still has to ask.

    Args:
        current: The result of ``values``.

    Returns:
        Unfilled trip fields and preferences, and ``people`` without a profile
        when the group is smaller than ``MIN_PEOPLE``.
    """
    refs = [ref for ref, value in current.items() if not value.filled]
    people = sum(1 for ref in current if ref.field is KnowledgeField.PEOPLE)
    if people < MIN_PEOPLE:
        refs.append(FieldRef(field=KnowledgeField.PEOPLE))
    return refs


def sources(
    current: Mapping[FieldRef, Value], by_assistant: Mapping[FieldRef, str]
) -> list[FieldSource]:
    """Tell who set each filled value.

    Args:
        current: The result of ``values``.
        by_assistant: Digest the assistant stored for each value it wrote.

    Returns:
        ``assistant`` where the stored digest still matches, else ``host``.
    """
    return [
        FieldSource(
            field=ref.field,
            profile_id=ref.profile_id,
            source=(
                ValueSource.ASSISTANT
                if by_assistant.get(ref) == value.digest
                else ValueSource.HOST
            ),
        )
        for ref, value in current.items()
        if value.filled
    ]
