"""Syntetyczne miasto testowe (15 miejsc), niezależne od arkusza miast.

Miejsca mają nazwy z dema ze specyfikacji (``docs/algorytm.md``, sekcja 7), ale
dane są wymyślone i stałe: aktualizacja arkusza miast nie zmienia wyniku testów
algorytmu. Pokryte przypadki: cena zweryfikowana, niezweryfikowana, darmowa i
nieznana (brak wiersza), godziny ze źródła, bez weryfikacji i nieznane, schody,
kolejka, miejsce za długie dla seniora i dziecka, restauracja indyjska, weto
(morska) oraz trzy noclegi z różnym stanem wymogów (spełniony, niespełniony,
niepotwierdzony).
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid5

from tuttitrip.accommodation.schemas import OfferFeatures
from tuttitrip.places.schemas import (
    Amenity,
    CityRead,
    Cuisine,
    DietTag,
    OpeningHours,
    PlaceCategory,
    PlaceHours,
    PlacePriceRead,
    PlaceRead,
    PlaceSource,
    PlaceTag,
    PriceUnit,
    TicketCategory,
    TimeRange,
    Weekday,
)

CITY_SLUG = "miasto-testowe"
CHECKED_AT = dt.datetime(2026, 9, 20, 9, 0, tzinfo=dt.UTC)
SOURCE_URL = "https://example.test/zrodlo"
_NAMESPACE = UUID("3b1c7a52-6d0e-4f55-8a2b-9c1d2e3f4a5b")

# Kolejność dni tygodnia jest częścią kontraktu (stały porządek kluczy w JSON).
_ALL_DAYS = tuple(Weekday)

LODGING_KEYS = ("apartament_basen", "hotel_centrum", "hostel_dworzec")
HOMESTAY = "apartament_basen"


def place_id(key: str) -> UUID:
    """Stałe id miejsca z klucza (te same wszędzie i w każdym uruchomieniu)."""
    return uuid5(_NAMESPACE, key)


def key_of(place: PlaceRead) -> str:
    """Klucz miejsca w fixtures (``source_key`` bez prefiksu ``test:``)."""
    return (place.source_key or "").removeprefix("test:")


def city() -> CityRead:
    return CityRead(
        slug=CITY_SLUG,
        name="Miasto Testowe",
        country="PL",
        timezone="Europe/Warsaw",
        currency="PLN",
        center_lat=52.0,
        center_lon=19.0,
        bbox_south=51.9,
        bbox_west=18.9,
        bbox_north=52.1,
        bbox_east=19.1,
    )


def _hours(
    open_: str,
    close: str,
    *,
    closed: tuple[Weekday, ...] = (),
    verified: bool = True,
) -> PlaceHours:
    weekly = {
        d: [TimeRange(open=open_, close=close)] for d in _ALL_DAYS if d not in closed
    }
    return PlaceHours(
        opening_hours=OpeningHours(weekly=weekly),
        source_url=SOURCE_URL if verified else None,
        verified=verified,
        checked_at=CHECKED_AT if verified else None,
    )


UNKNOWN_HOURS = PlaceHours(
    opening_hours=None, source_url=None, verified=False, checked_at=None
)


def _price(
    amount: str,
    *,
    category: TicketCategory = TicketCategory.ADULT,
    unit: PriceUnit = PriceUnit.PERSON,
    verified: bool = True,
    family_size: int | None = None,
) -> PlacePriceRead:
    return PlacePriceRead(
        ticket_category=category,
        unit=unit,
        age_min=None,
        age_max=None,
        family_size=family_size,
        amount=Decimal(amount),
        currency="PLN",
        source_url=SOURCE_URL if verified else None,
        verified=verified,
        checked_at=CHECKED_AT if verified else None,
    )


def _place(  # ruff: ignore[too-many-arguments] flat record of the per-place inputs
    index: int,
    key: str,
    name: str,
    category: PlaceCategory,
    tags: list[PlaceTag],
    *,
    hours: PlaceHours,
    prices: list[PlacePriceRead],
    visit_min: int,
    segment_km: float,
    transfer_min: int = 10,
    queue_min: int = 0,
    stairs: float = 0.0,
    indoor: bool | None = None,
    iconic: bool = False,
    cuisine: Cuisine | None = None,
    diet_tags: list[DietTag] | None = None,
    amenities: list[Amenity] | None = None,
) -> PlaceRead:
    return PlaceRead(
        id=place_id(key),
        city_slug=CITY_SLUG,
        name=name,
        category=category,
        tags=tags,
        lat=52.0 + index * 0.005,
        lon=19.0 + index * 0.004,
        osm_type=None,
        osm_id=None,
        google_place_id=None,
        hours=hours,
        prices=prices,
        typical_visit_min=visit_min,
        segment_km=segment_km,
        transfer_min=transfer_min,
        queue_min=queue_min,
        stairs=stairs,
        wheelchair=stairs == 0,
        indoor=indoor,
        iconic=iconic,
        cuisine=cuisine,
        diet_tags=diet_tags or [],
        amenities=amenities or [],
        source_key=f"test:{key}",
        source=PlaceSource.SHEET,
    )


def places() -> dict[str, PlaceRead]:
    """Miejsca miasta po kluczu, w stałej kolejności (12 miejsc i 3 noclegi)."""
    cat, tag = PlaceCategory, PlaceTag
    mon = (Weekday.MON,)
    rows = [
        _place(
            0,
            "muzeum_miejskie",
            "Muzeum Miejskie",
            cat.MUSEUM,
            [tag.HISTORY, tag.MUSEUMS, tag.ARCHITECTURE],
            hours=_hours("10:00", "18:00", closed=mon),
            prices=[
                _price("30"),
                _price("15", category=TicketCategory.CHILD),
                _price("20", category=TicketCategory.SENIOR),
            ],
            visit_min=90,
            segment_km=0.6,
            queue_min=10,
            indoor=True,
            iconic=True,
        ),
        _place(
            1,
            "hevelianum",
            "Centrum Nauki Hevelianum",
            cat.ATTRACTION,
            [tag.SCIENCE, tag.KIDS, tag.FAMILY, tag.MUSEUMS],
            hours=_hours("09:00", "17:00"),
            prices=[_price("50"), _price("35", category=TicketCategory.CHILD)],
            visit_min=150,
            segment_km=1.2,
            queue_min=25,
            stairs=0.3,
            indoor=True,
            iconic=True,
        ),
        _place(
            2,
            "westerplatte",
            "Półwysep Wschodni",
            cat.ATTRACTION,
            [tag.HISTORY, tag.VIEWS, tag.NATURE],
            hours=_hours("08:00", "20:00", verified=False),
            prices=[_price("40", verified=False)],
            visit_min=120,
            segment_km=2.6,
            transfer_min=25,
            stairs=0.2,
            indoor=False,
            iconic=True,
        ),
        _place(
            3,
            "park_oliwski",
            "Park Oliwski",
            cat.PARK,
            [tag.PARKS, tag.NATURE, tag.RELAXATION, tag.PLAYGROUND],
            hours=_hours("06:00", "22:00"),
            prices=[_price("0")],
            visit_min=75,
            segment_km=0.9,
            indoor=False,
        ),
        _place(
            4,
            "planszowki",
            "Kawiarnia z planszówkami",
            cat.ENTERTAINMENT,
            [tag.FAMILY, tag.KIDS, tag.RELAXATION],
            hours=_hours("12:00", "22:00"),
            prices=[_price("25")],
            visit_min=120,
            segment_km=0.2,
            indoor=True,
        ),
        _place(
            5,
            "bar_mleczny",
            "Bar mleczny",
            cat.RESTAURANT,
            [tag.LOCAL_FOOD],
            hours=_hours("08:00", "18:00", closed=(Weekday.SUN,)),
            prices=[_price("28")],
            visit_min=45,
            segment_km=0.2,
            queue_min=15,
            indoor=True,
            cuisine=Cuisine.POLISH,
            diet_tags=[DietTag.VEGETARIAN],
        ),
        _place(
            6,
            "kawiarnia_w_ogrodzie",
            "Kawiarnia w ogrodzie",
            cat.CAFE,
            [tag.RELAXATION],
            hours=UNKNOWN_HOURS,
            prices=[_price("22")],
            visit_min=45,
            segment_km=0.1,
            indoor=False,
            diet_tags=[DietTag.VEGETARIAN, DietTag.VEGAN, DietTag.GLUTEN_FREE],
        ),
        _place(
            7,
            "pizzeria",
            "Pizzeria",
            cat.RESTAURANT,
            [tag.STREET_FOOD, tag.FAMILY],
            hours=_hours("12:00", "23:00"),
            prices=[_price("45")],
            visit_min=60,
            segment_km=0.2,
            indoor=True,
            cuisine=Cuisine.ITALIAN,
            diet_tags=[DietTag.VEGETARIAN],
        ),
        _place(
            8,
            "restauracja_indyjska",
            "Restauracja indyjska",
            cat.RESTAURANT,
            [tag.STREET_FOOD, tag.LOCAL_FOOD],
            hours=_hours("12:00", "22:00", closed=mon),
            prices=[_price("60", verified=False)],
            visit_min=75,
            segment_km=0.3,
            indoor=True,
            cuisine=Cuisine.INDIAN,
            diet_tags=[DietTag.VEGETARIAN, DietTag.VEGAN, DietTag.HALAL],
        ),
        _place(
            9,
            "restauracja_morska",
            "Restauracja morska",
            cat.RESTAURANT,
            [tag.LOCAL_FOOD, tag.VIEWS],
            hours=_hours("12:00", "23:00"),
            prices=[_price("95")],
            visit_min=90,
            segment_km=0.3,
            stairs=0.2,
            indoor=True,
            cuisine=Cuisine.INTERNATIONAL,
            diet_tags=[DietTag.PESCATARIAN],
        ),
        _place(
            10,
            "wieza_widokowa",
            "Wieża widokowa",
            cat.VIEWPOINT,
            [tag.VIEWS, tag.ARCHITECTURE, tag.ADVENTURE],
            hours=_hours("10:00", "19:00"),
            prices=[_price("15", verified=False)],
            visit_min=45,
            segment_km=0.4,
            queue_min=30,
            stairs=1.0,
            indoor=False,
        ),
        _place(
            11,
            "zoo",
            "Ogród zoologiczny",
            cat.ATTRACTION,
            [tag.ANIMALS, tag.KIDS, tag.FAMILY, tag.NATURE],
            hours=_hours("09:00", "17:00", verified=False),
            prices=[],  # nieznana cena: brak wiersza (E6 zawyża ją o delta)
            visit_min=150,
            segment_km=1.4,
            queue_min=15,
            indoor=False,
        ),
        _place(
            12,
            "apartament_basen",
            "Apartament z basenem",
            cat.LODGING,
            [],
            hours=UNKNOWN_HOURS,
            prices=[
                _price(
                    "300",
                    category=TicketCategory.FAMILY,
                    unit=PriceUnit.NIGHT,
                    family_size=4,
                )
            ],
            visit_min=0,
            segment_km=0.1,
            transfer_min=0,
            indoor=True,
            amenities=[Amenity.POOL, Amenity.KITCHEN, Amenity.WIFI],
        ),
        _place(
            13,
            "hotel_centrum",
            "Hotel w centrum",
            cat.LODGING,
            [],
            hours=UNKNOWN_HOURS,
            prices=[
                _price(
                    "380",
                    category=TicketCategory.FAMILY,
                    unit=PriceUnit.NIGHT,
                    family_size=4,
                )
            ],
            visit_min=0,
            segment_km=0.1,
            transfer_min=0,
            indoor=True,
            amenities=[
                Amenity.WIFI,
                Amenity.ELEVATOR,
                Amenity.WHEELCHAIR_ACCESSIBLE,
                Amenity.BREAKFAST,
            ],
        ),
        _place(
            14,
            "hostel_dworzec",
            "Hostel przy dworcu",
            cat.LODGING,
            [],
            hours=UNKNOWN_HOURS,
            prices=[
                _price(
                    "160",
                    category=TicketCategory.FAMILY,
                    unit=PriceUnit.NIGHT,
                    family_size=4,
                    verified=False,
                )
            ],
            visit_min=0,
            segment_km=0.1,
            transfer_min=0,
            indoor=True,
            amenities=[Amenity.WIFI],
        ),
    ]
    return {key_of(row): row for row in rows}


def lodging_offers() -> dict[str, OfferFeatures]:
    """Co wiemy o noclegach: obecne i potwierdzone jako nieobecne cechy.

    Winda u apartamentu jest niepotwierdzona (kontrakt 3-stanowy), basen w
    hotelu i hostelu potwierdzenie nieobecny.
    """
    return {
        "apartament_basen": OfferFeatures(
            present={"pool", "kitchen", "wifi"}, absent={"wheelchair_accessible"}
        ),
        "hotel_centrum": OfferFeatures(
            present={"wifi", "elevator", "wheelchair_accessible", "breakfast"},
            absent={"pool"},
        ),
        "hostel_dworzec": OfferFeatures(
            present={"wifi"}, absent={"pool", "elevator", "wheelchair_accessible"}
        ),
    }
