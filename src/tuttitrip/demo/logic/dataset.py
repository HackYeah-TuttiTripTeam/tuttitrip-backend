"""The sample data of the demo account: four trips, the Warszawa one in full.

Plain data, no I/O. The first person of every trip is the demo account's own
profile (the host); the others are people without an account. Dates are
offsets from the day of the reset, so the trips are always in the future.
"""

from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal

from tuttitrip.places.schemas import Amenity, Cuisine, DietTag, PlaceTag
from tuttitrip.planning.linter.schemas import NamedDay, NamedPlan, NamedStop
from tuttitrip.profiles.preferences.schemas import (
    Constraints,
    Diet,
    ImportancePool,
    MinTag,
    MinTagDomain,
    PreferencesWrite,
)
from tuttitrip.profiles.schemas import ProfileWeightPreset
from tuttitrip.trips.schemas import TripCreate

# Auth0 name of the shared demo account; the daily reset puts it back, since
# every juror may rename the account (PATCH /me/account).
DEMO_ACCOUNT_NAME = "Konto demo"


@dataclass(frozen=True)
class PersonSeed:
    """One person on a demo trip."""

    name: str
    age: int
    interests: dict[PlaceTag, float]
    pool: tuple[int, int, int, int, int]  # lodging, food, attractions, pace, cost
    diet: tuple[DietTag, ...] = ()
    allergies: tuple[str, ...] = ()
    min_tags: tuple[MinTag, ...] = ()
    stairs: bool = False
    # Thumb ratings on catalog places, if the catalog has them: how many of the
    # city's first places this person wants, and how many they do not.
    wants: int = 0
    dont_wants: int = 0

    def preferences(self) -> PreferencesWrite:
        """The person's preferences as the API payload.

        Returns:
            The whole preferences of the person.
        """
        lodging, food, attractions, pace, cost = self.pool
        return PreferencesWrite(
            interests=dict(self.interests),
            diet=Diet(tags=list(self.diet), allergies=list(self.allergies)),
            min_tags=list(self.min_tags),
            constraints=Constraints(stairs=self.stairs),
            importance_pool=ImportancePool(
                lodging=lodging,
                food=food,
                attractions=attractions,
                pace=pace,
                cost=cost,
            ),
        )


WEEKDAYS = (
    "poniedziałek",
    "wtorek",
    "środa",
    "czwartek",
    "piątek",
    "sobota",
    "niedziela",
)
"""Polish weekday names, Monday first (``date.weekday()``)."""


@dataclass(frozen=True)
class StopSeed:
    """One stop of the chatbot's plan: a name as a chatbot writes it, and the times."""

    name: str
    start: time
    end: time


@dataclass(frozen=True)
class TripSeed:
    """One demo trip."""

    name: str
    destination: str
    city_slug: str
    starts_in_days: int
    nights: int
    budget_total: tuple[int, int]
    people: tuple[PersonSeed, ...]
    hard_amenities: tuple[Amenity, ...] = ()
    weights: ProfileWeightPreset | None = None
    # Index in `people` of the person the weight preset focuses on.
    focus: int | None = None
    flex_pct: int = 0
    # Compute and store the plan at the reset (needs the city in the catalog).
    plan: bool = False
    # The plan another chatbot wrote for the group, day by day (scene 2:30), and
    # the text of a lodging offer, both stored as pasted documents.
    chatbot_days: tuple[tuple[StopSeed, ...], ...] = ()
    offer_text: str | None = None

    def create_payload(self, today: date) -> TripCreate:
        """The trip as the creation payload.

        Args:
            today: The day of the reset.

        Returns:
            Trip details with dates counted from ``today``.
        """
        start = today + timedelta(days=self.starts_in_days)
        low, high = self.budget_total
        return TripCreate(
            name=self.name,
            destination=self.destination,
            city_slug=self.city_slug,
            currency="PLN",
            start_date=start,
            end_date=start + timedelta(days=self.nights),
            budget_total_min=Decimal(low),
            budget_total_max=Decimal(high),
            budget_flex_pct=self.flex_pct,
        )

    def chatbot_plan(self, today: date) -> NamedPlan | None:
        """The chatbot's plan as the linter's structure, on the trip's dates.

        Args:
            today: The day of the reset.

        Returns:
            The plan, or None when the trip has none.
        """
        if not self.chatbot_days:
            return None
        start = today + timedelta(days=self.starts_in_days)
        return NamedPlan(
            days=[
                NamedDay(
                    day=start + timedelta(days=index),
                    items=[
                        NamedStop(name=s.name, start=s.start, end=s.end) for s in stops
                    ],
                )
                for index, stops in enumerate(self.chatbot_days)
            ]
        )

    def chatbot_text(self, today: date) -> str | None:
        """The chatbot's plan as the text a host pastes (rendered from the data).

        Args:
            today: The day of the reset.

        Returns:
            Plain text, one line per stop, or None when the trip has none.
        """
        plan = self.chatbot_plan(today)
        if plan is None:
            return None
        lines: list[str] = []
        for number, day in enumerate(plan.days, start=1):
            lines.append(
                f"Dzień {number} ({WEEKDAYS[day.day.weekday()]} {day.day:%d.%m})"
            )
            lines.extend(
                f"{s.start:%H:%M}-{s.end:%H:%M} {s.name}"
                for s in day.items
                if s.end is not None
            )
        return "\n".join(lines)


OLA = PersonSeed(
    name="Ola",
    age=38,
    interests={
        PlaceTag.ARCHITECTURE: 0.8,
        PlaceTag.MUSEUMS: 0.7,
        PlaceTag.LOCAL_FOOD: 0.9,
        PlaceTag.PARKS: 0.5,
    },
    pool=(2, 3, 3, 1, 1),
    min_tags=(MinTag(domain=MinTagDomain.FOOD, tag=Cuisine.POLISH),),
    wants=2,
)
KASIA = PersonSeed(
    name="Kasia",
    age=6,
    interests={PlaceTag.KIDS: 1.0, PlaceTag.ANIMALS: 0.9, PlaceTag.PLAYGROUND: 0.9},
    pool=(2, 2, 4, 1, 1),
    allergies=("orzechy",),
    diet=(DietTag.NUT_FREE,),
    wants=2,
    dont_wants=1,
)
TOMEK = PersonSeed(
    name="Tomek",
    age=13,
    interests={
        PlaceTag.SCIENCE: 0.9,
        PlaceTag.HISTORY: 0.6,
        PlaceTag.ADVENTURE: 0.8,
        PlaceTag.STREET_FOOD: 0.7,
    },
    pool=(1, 2, 5, 1, 1),
    wants=2,
    dont_wants=1,
)
BABCIA = PersonSeed(
    name="Babcia Halina",
    age=72,
    interests={
        PlaceTag.HISTORY: 0.8,
        PlaceTag.PARKS: 0.9,
        PlaceTag.RELIGION: 0.6,
        PlaceTag.RELAXATION: 0.8,
    },
    pool=(3, 2, 2, 2, 1),
    stairs=True,
    wants=1,
    dont_wants=1,
)


def _duo(name: str, age: int, interests: dict[PlaceTag, float]) -> PersonSeed:
    return PersonSeed(
        name=name, age=age, interests=interests, pool=(2, 3, 3, 1, 1), wants=1
    )


FAMILY = (OLA, KASIA, TOMEK, BABCIA)
FAMILY_AMENITIES = (Amenity.FAMILY_ROOM, Amenity.ELEVATOR, Amenity.POOL)
"""The family needs a pool in every lodging (the scene 2:30 requirement)."""
FAMILY_FLEX_PCT = 10
"""Section 7 of the specification: ``B_max = B_do * 1.10``."""


def _stop(name: str, start: str, end: str) -> StopSeed:
    return StopSeed(name, time.fromisoformat(start), time.fromisoformat(end))


CHATBOT_DAYS: tuple[tuple[StopSeed, ...], ...] = (
    (
        _stop("Zamek Królewski", "09:00", "11:30"),
        _stop("Stare Miasto", "11:30", "13:00"),
        _stop("Bar mleczny", "13:00", "14:00"),
        _stop("Pałac Kultury i Nauki", "14:15", "16:00"),
        _stop("Muzeum Narodowe", "16:00", "18:30"),
        _stop("Podwodny Park Wodny Wilanów", "19:00", "20:30"),
    ),
    (
        _stop("Łazienki Królewskie", "08:00", "11:00"),
        _stop("Centrum Nauki Kopernik", "11:15", "15:00"),
        _stop("Zoo", "15:30", "18:00"),
        _stop("Bulwary Wiślane", "18:00", "19:30"),
    ),
    (
        _stop("Muzeum Powstania Warszawskiego", "09:00", "12:00"),
        _stop("Wilanów", "12:30", "16:00"),
        _stop("Park Skaryszewski", "16:15", "17:30"),
    ),
)
"""A chatbot's plan, prepared in advance and never tuned in our favour."""
OFFER_TEXT = (
    "Apartament Rodzinny Praga, Warszawa. Dwie sypialnie dla czterech osób, "
    "pokój rodzinny, winda w budynku, śniadanie w cenie. 480 zł za noc. "
    "Basen: brak, w pobliżu pływalnia miejska. Parking płatny 40 zł za dobę."
)
"""A lodging offer that breaks the pool requirement (scene 2:30)."""

DEMO_TRIPS: tuple[TripSeed, ...] = (
    TripSeed(
        name="Berlin na weekend",
        destination="Berlin",
        city_slug="berlin",
        starts_in_days=75,
        nights=2,
        budget_total=(1800, 2400),
        people=(
            _duo("Ola", 38, {PlaceTag.ART: 0.9, PlaceTag.NIGHTLIFE: 0.6}),
            _duo("Michał", 40, {PlaceTag.HISTORY: 0.9, PlaceTag.MUSEUMS: 0.8}),
        ),
    ),
    TripSeed(
        name="Kraków ze znajomymi",
        destination="Kraków",
        city_slug="krakow",
        starts_in_days=50,
        nights=2,
        budget_total=(1500, 2100),
        people=(
            _duo("Ola", 38, {PlaceTag.HISTORY: 0.8, PlaceTag.LOCAL_FOOD: 0.9}),
            _duo("Ewa", 35, {PlaceTag.ART: 0.7, PlaceTag.MARKETS: 0.8}),
            _duo("Paweł", 37, {PlaceTag.SPORT: 0.6, PlaceTag.NIGHTLIFE: 0.7}),
        ),
    ),
    TripSeed(
        name="Gdańsk nad morzem",
        destination="Gdańsk",
        city_slug="gdansk",
        starts_in_days=30,
        nights=3,
        budget_total=(1600, 2200),
        people=(
            _duo("Ola", 38, {PlaceTag.BEACHES: 0.9, PlaceTag.RELAXATION: 0.8}),
            _duo("Jacek", 41, {PlaceTag.WATER: 0.8, PlaceTag.CYCLING: 0.7}),
        ),
        hard_amenities=(Amenity.PARKING,),
    ),
    TripSeed(
        name="Warszawa z rodziną, budżet 900 do 1100 zł",
        destination="Warszawa",
        city_slug="warszawa",
        starts_in_days=21,
        nights=2,
        budget_total=(900, 1100),
        people=FAMILY,
        hard_amenities=FAMILY_AMENITIES,
        weights=ProfileWeightPreset.DZIEN_BABCI,
        focus=3,
        flex_pct=FAMILY_FLEX_PCT,
        plan=True,
    ),
    TripSeed(
        name="Warszawa z rodziną",
        destination="Warszawa",
        city_slug="warszawa",
        starts_in_days=21,
        nights=2,
        budget_total=(1300, 1700),
        people=FAMILY,
        hard_amenities=FAMILY_AMENITIES,
        weights=ProfileWeightPreset.DZIEN_BABCI,
        focus=3,
        flex_pct=FAMILY_FLEX_PCT,
        plan=True,
        chatbot_days=CHATBOT_DAYS,
        offer_text=OFFER_TEXT,
    ),
)
"""Oldest first: the Warszawa trip is created last, so it tops the list."""
