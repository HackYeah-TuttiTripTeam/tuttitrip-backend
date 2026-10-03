"""Scenariusze algorytmu: wyjazd + grupa + wymogi noclegu w jednym obiekcie.

Cztery grupy i pięć scenariuszy. ``reference`` i ``solo`` odtwarzają dane z
sekcji 7 ``docs/algorytm.md``, ``over_budget`` to ta sama rodzina z budżetem
900 do 1100 zł (test zgody na przekroczenie, E6), ``friends`` i ``accessible``
dają rozbieżne gusty i twarde ograniczenia dostępności. ``flex`` wynosi 10%
(15% u ``accessible``), czyli ``B_max = B_do · (1 + flex)``.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from tests.fixtures.city import CITY_SLUG
from tests.fixtures.personas import (
    Group,
    accessible_family,
    friends,
    reference_family,
    solo_traveller,
)
from tuttitrip.accommodation.logic.keys import RequirementKind
from tuttitrip.accommodation.schemas import RequirementItem, RequirementsWrite
from tuttitrip.trips.schemas import TripCreate

FRIDAY = dt.date(2026, 10, 9)
SATURDAY = dt.date(2026, 10, 10)
SUNDAY = dt.date(2026, 10, 11)


@dataclass(frozen=True, slots=True)
class Scenario:
    """Jeden przypadek dla algorytmu: wyjazd, grupa i wymogi noclegu."""

    key: str
    trip: TripCreate
    group: Group
    requirements: RequirementsWrite

    @property
    def days(self) -> int:
        start, end = self.trip.start_date, self.trip.end_date
        return 0 if start is None or end is None else (end - start).days + 1

    @property
    def b_do(self) -> Decimal:
        return self.trip.budget_total_max or Decimal(0)

    @property
    def b_max(self) -> Decimal:
        """``B_max = B_do · (1 + flex)`` (E0, E6)."""
        flex = Decimal(self.trip.budget_flex_pct or 0) / 100
        return self.b_do * (1 + flex)


def _amenity(key: str, *, hard: bool) -> RequirementItem:
    return RequirementItem(kind=RequirementKind.AMENITY, key=key, hard=hard)


def _trip(
    name: str,
    start: dt.date,
    end: dt.date,
    budget: tuple[int, int],
    flex_pct: int = 10,
) -> TripCreate:
    return TripCreate(
        name=name,
        destination="Miasto Testowe",
        city_slug=CITY_SLUG,
        currency="PLN",
        start_date=start,
        end_date=end,
        day_start=dt.time(9, 0),
        day_end=dt.time(20, 0),
        budget_total_min=Decimal(budget[0]),
        budget_total_max=Decimal(budget[1]),
        budget_flex_pct=flex_pct,
        fairness_alpha=1.0,
    )


def _scenario(
    key: str, trip: TripCreate, group: Group, *requirements: RequirementItem
) -> Scenario:
    return Scenario(
        key, trip, group, RequirementsWrite(requirements=list(requirements))
    )


def reference() -> Scenario:
    """Sekcja 7: 4 osoby, 3 dni, 1300 do 1700 zł, twardy wymóg basenu."""
    return _scenario(
        "reference",
        _trip("Rodzina referencyjna", FRIDAY, SUNDAY, (1300, 1700)),
        reference_family(),
        _amenity("pool", hard=True),
        _amenity("elevator", hard=False),
    )


def solo() -> Scenario:
    """Sekcja 7, tryb solo: 2 dni, 500 do 800 zł, bez noclegu (wyjście bez bazy)."""
    return _scenario(
        "solo", _trip("Solo", SATURDAY, SUNDAY, (500, 800)), solo_traveller()
    )


def over_budget() -> Scenario:
    """Ta sama rodzina z budżetem 900 do 1100 zł: test zgody na przekroczenie."""
    return _scenario(
        "over_budget",
        _trip("Rodzina na budżecie", FRIDAY, SUNDAY, (900, 1100)),
        reference_family(),
        _amenity("pool", hard=True),
        _amenity("elevator", hard=False),
    )


def friends_scenario() -> Scenario:
    """Troje dorosłych o rozbieżnych gustach (tu alfa naprawdę ma znaczenie)."""
    return _scenario(
        "friends",
        _trip("Przyjaciele", FRIDAY, SUNDAY, (1200, 1800)),
        friends(),
        _amenity("wifi", hard=False),
    )


def accessible() -> Scenario:
    """Wózek i dwulatka: twarda winda i dostępność, miejsca ze schodami odpadają."""
    return _scenario(
        "accessible",
        _trip("Dostępny weekend", SATURDAY, SUNDAY, (1500, 2000), flex_pct=15),
        accessible_family(),
        _amenity("elevator", hard=True),
        _amenity("wheelchair_accessible", hard=True),
    )


def all_scenarios() -> dict[str, Scenario]:
    return {
        s.key: s
        for s in (reference(), solo(), over_budget(), friends_scenario(), accessible())
    }
