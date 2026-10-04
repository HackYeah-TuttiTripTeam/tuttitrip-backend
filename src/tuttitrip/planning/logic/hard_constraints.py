"""Hard constraints on places (docs/algorytm.md, E0), never relaxed.

This module filters candidate places. A rejected place gets a reason code per
cause (and per person where one is to blame). What E0 checks on the whole plan
is left to the solver (backend#47): the budget ``c(P) <= B_max`` and the daily
distance ``<= 1.5 * D_i`` (``schedule.schedule_day`` already checks the latter
for a single day). The "wheelchair" exclusion of places with stairs is also the
solver's call (backend#45).

Opening hours and the day window reuse ``schedule.schedule_day`` with the place
alone: a place is a candidate when it can be visited on at least one day.
Lodging places are not candidates (the stay is a separate decision).
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from tuttitrip.places.schemas import PlaceCategory, PlaceRead
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.logic.schedule import (
    DayWindow,
    Infeasible,
    InfeasibleCode,
    schedule_day,
)
from tuttitrip.planning.schemas import PlanningInput, PlanningPerson, PlanningTrip

_EPS = 1e-9


class RejectionCode(StrEnum):
    """Why a place is not a candidate."""

    VETO = "veto"
    CLOSED = "closed"
    NO_FIT = "no_fit"
    SEGMENT = "segment"
    STAIRS = "stairs"


@dataclass(frozen=True, slots=True)
class Rejection:
    """One reason a place is rejected; ``person_id`` is set when one is to blame."""

    place_id: UUID
    code: RejectionCode
    person_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Candidates:
    """Result of E0 on the places.

    ``rejections`` holds every reason of every rejected place, sorted by place
    id, code and person. ``must_blocked`` lists the "must" ids that are not
    accepted (rejected, unknown or lodging): no plan can satisfy them, so the
    caller must report a conflict.
    """

    accepted: tuple[PlaceRead, ...]
    rejections: tuple[Rejection, ...]
    must_blocked: tuple[UUID, ...]

    def reasons(self, place_id: UUID) -> tuple[Rejection, ...]:
        """Reasons a place was rejected.

        Args:
            place_id: The place.

        Returns:
            The reasons, empty when the place was accepted.
        """
        return tuple(r for r in self.rejections if r.place_id == place_id)


def _person_rejections(
    person: PlanningPerson, place: PlaceRead, params: AlgorithmParams
) -> list[Rejection]:
    found: list[Rejection] = []
    if place.id in person.vetoes:
        found.append(Rejection(place.id, RejectionCode.VETO, person.id))
    # Separate from schedule.DAILY_KM_FACTOR: this is the per-place segment
    # (s_i), that one is the whole day's distance (D_i).
    if place.segment_km > params.segment_factor * person.segment_km + _EPS:
        found.append(Rejection(place.id, RejectionCode.SEGMENT, person.id))
    if place.stairs * person.stairs_sensitivity >= params.stairs_limit - _EPS:
        found.append(Rejection(place.id, RejectionCode.STAIRS, person.id))
    return found


def _time_rejection(
    place: PlaceRead, trip: PlanningTrip, zone: ZoneInfo
) -> Rejection | None:
    # Open and fitting on at least one day of the trip. NO_FIT wins over CLOSED:
    # a place that fits no window on a day it is open is the more telling reason.
    codes = set()
    for day in trip.days:
        window = DayWindow(day, zone, trip.day_start, trip.day_end)
        result = schedule_day([place], window, [])
        if not isinstance(result, Infeasible):
            return None
        codes.add(result.code)
    code = (
        RejectionCode.NO_FIT if InfeasibleCode.NO_FIT in codes else RejectionCode.CLOSED
    )
    return Rejection(place.id, code)


def filter_places(
    data: PlanningInput, params: AlgorithmParams = DEFAULT_PARAMS
) -> Candidates:
    """Apply E0 to the candidate places.

    A place is rejected when anyone vetoes it, anyone would walk more than
    ``1.5 * s_i`` at it, ``stairs_p * sensitivity_i`` reaches 0.9 for anyone
    (everybody takes part in everything), or it cannot be visited on any day
    of the trip within the opening hours and the day window.

    Args:
        data: The planning input.
        params: Algorithm parameters.

    Returns:
        Accepted places in input order and all rejection reasons.
    """
    zone = ZoneInfo(data.trip.timezone)
    accepted: list[PlaceRead] = []
    rejections: list[Rejection] = []
    for place in data.places:
        if place.category is PlaceCategory.LODGING:
            continue
        found = [
            r
            for person in data.people
            for r in _person_rejections(person, place, params)
        ]
        time_reason = _time_rejection(place, data.trip, zone)
        if time_reason is not None:
            found.append(time_reason)
        if found:
            rejections.extend(found)
        else:
            accepted.append(place)
    ordered = sorted(
        rejections,
        key=lambda r: (str(r.place_id), r.code.value, str(r.person_id or "")),
    )
    # A "must" id that is rejected, unknown or a lodging cannot be satisfied.
    usable = {p.id for p in accepted}
    return Candidates(
        accepted=tuple(accepted),
        rejections=tuple(ordered),
        must_blocked=tuple(sorted(data.must - usable, key=str)),
    )
