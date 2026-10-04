"""The sample trip every new account gets: one family weekend in Warszawa.

Plain data, no I/O. The host's own profile is Ola; the others are people
without an account. The trip is labelled "Przykład: ..." (or "Sample: ..."),
so nobody mistakes it for their own plan. Dates start on the first Friday at
least two weeks ahead, so the weekdays (and with them the opening hours the
plan uses) are the same for every copy made in the same week.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from tuttitrip.demo.logic.dataset import BABCIA, KASIA, OLA, TOMEK, PersonSeed, TripSeed
from tuttitrip.expenses.schemas import ExpenseCategory
from tuttitrip.places.schemas import Amenity
from tuttitrip.profiles.schemas import ProfileWeightPreset

Locale = Literal["pl", "en"]
DEFAULT_LOCALE: Locale = "pl"

MIN_DAYS_AHEAD = 14
FRIDAY = 4  # date.weekday()
NIGHTS = 2


@dataclass(frozen=True)
class ExpenseSeed:
    """One expense of the sample trip; people are named by display name."""

    description: str
    amount: Decimal
    payer: str
    participants: tuple[str, ...]
    category: ExpenseCategory
    day_offset: int  # days after the start of the trip


@dataclass(frozen=True)
class SampleTrip:
    """Everything the copy needs in one language."""

    trip: TripSeed
    expenses: tuple[ExpenseSeed, ...]


def locale_from_header(accept_language: str | None) -> Locale:
    """The sample's language from an ``Accept-Language`` header.

    Args:
        accept_language: The header value, if any.

    Returns:
        ``en`` when the first listed language is English, otherwise ``pl``.
    """
    first = (accept_language or "").split(",")[0].split(";")[0].strip().lower()
    return "en" if first == "en" or first.startswith("en-") else DEFAULT_LOCALE


def first_friday(today: date) -> date:
    """The first Friday at least ``MIN_DAYS_AHEAD`` days after ``today``.

    Args:
        today: The day of the copy.

    Returns:
        The start of the sample trip.
    """
    earliest = today + timedelta(days=MIN_DAYS_AHEAD)
    return earliest + timedelta(days=(FRIDAY - earliest.weekday()) % 7)


def _english(person: PersonSeed) -> PersonSeed:
    names = {"Babcia Halina": "Grandma Halina"}
    allergies = {"orzechy": "nuts"}
    return replace(
        person,
        name=names.get(person.name, person.name),
        allergies=tuple(allergies.get(a, a) for a in person.allergies),
    )


def sample_trip(locale: Locale, city_slug: str, today: date) -> SampleTrip:
    """The sample trip in the given language, dated from ``today``.

    Args:
        locale: Language of the names and descriptions.
        city_slug: Catalog city the plan is computed for.
        today: The day of the copy.

    Returns:
        The trip seed and its expenses.
    """
    english = locale == "en"
    people = tuple(_english(p) if english else p for p in (OLA, KASIA, TOMEK, BABCIA))
    ola, kasia, tomek, babcia = (p.name for p in people)
    trip = TripSeed(
        name="Sample: Warsaw with the family"
        if english
        else "Przykład: Warszawa z rodziną",
        destination="Warsaw" if english else "Warszawa",
        city_slug=city_slug,
        starts_in_days=(first_friday(today) - today).days,
        nights=NIGHTS,
        budget_total=(1300, 1700),
        people=people,
        hard_amenities=(Amenity.FAMILY_ROOM, Amenity.ELEVATOR),
        weights=ProfileWeightPreset.DZIEN_BABCI,
        focus=3,
    )
    everyone = (ola, kasia, tomek, babcia)
    expenses = (
        ExpenseSeed(
            "Train tickets" if english else "Bilety na pociąg",
            Decimal("312.00"),
            ola,
            everyone,
            ExpenseCategory.TRANSPORT,
            0,
        ),
        ExpenseSeed(
            "Dinner in the Old Town" if english else "Obiad na Starym Mieście",
            Decimal("246.50"),
            babcia,
            everyone,
            ExpenseCategory.FOOD,
            1,
        ),
        ExpenseSeed(
            "Science Centre tickets" if english else "Bilety do Centrum Nauki",
            Decimal("90.00"),
            tomek,
            (ola, kasia, tomek),
            ExpenseCategory.ACTIVITIES,
            1,
        ),
    )
    return SampleTrip(trip=trip, expenses=expenses)
