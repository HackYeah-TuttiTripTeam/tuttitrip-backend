"""Parser and validator of the city sheet (``miasta.xlsx``).

The sheet is the contract between the team's spreadsheet and the catalog:
sheet names and column headers must not drift. This module is pure: it takes
the cell values of every sheet and returns catalog rows, or raises one
:class:`SheetImportError` listing *every* bad cell with its sheet and row
number, so a broken edit is fixed in one go and nothing is written half way.

Rules (see the sheet's ``README``):

* every place, lodging and fare row names a source (``url_zrodla``) and the
  date it was checked (``data_sprawdzenia``);
* an empty price or hours cell means unknown: no price row, no hours. Nothing
  is guessed, and ``*_zweryfikowane`` is the only source of the verified mark;
* the sheet's dictionary is wider than the backend enums, so categories and
  tags go through explicit maps. A value outside the map aborts the import.
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tuttitrip.places.logic.osm_hours import parse_closed_dates, parse_osm_hours
from tuttitrip.places.schemas import (
    Amenity,
    Cuisine,
    DietTag,
    OpeningHours,
    OsmType,
    PlaceCategory,
    PlaceTag,
    PriceUnit,
    TicketCategory,
)

type Cell = str | int | float | bool | date | datetime | None

PLACES = "miejsca"
LODGINGS = "noclegi"
FARES = "komunikacja"
CITIES = "miasta"

# Required headers per sheet. Extra columns are ignored; optional ones
# (added to the sheet later or not needed) are listed in OPTIONAL_COLUMNS.
REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    CITIES: (
        "slug",
        "nazwa",
        "kraj",
        "strefa_czasowa",
        "waluta",
        "srodek_lat",
        "srodek_lon",
    ),
    PLACES: (
        "klucz",
        "miasto",
        "nazwa",
        "szerokosc",
        "dlugosc",
        "osm_typ",
        "osm_id",
        "kategoria",
        "tagi",
        "godziny_osm",
        "dni_zamkniecia",
        "czas_wizyty_min",
        "kryte",
        "kultowe",
        "dla_dzieci",
        "waluta",
        "cena_normalna",
        "cena_ulgowa",
        "cena_dziecko",
        "cena_senior",
        "cena_student",
        "cena_rodzinna",
        "zasady_rodzinnego",
        "wozek",
        "schody",
        "url_zrodla",
        "data_sprawdzenia",
        "godziny_zweryfikowane",
        "ceny_zweryfikowane",
    ),
    LODGINGS: (
        "klucz",
        "miasto",
        "nazwa",
        "szerokosc",
        "dlugosc",
        "osm_typ",
        "osm_id",
        "typ",
        "cena_za_noc_od",
        "waluta",
        "basen",
        "kuchnia",
        "parking",
        "pokoj_rodzinny",
        "dostepnosc_wozek",
        "url_zrodla",
        "data_sprawdzenia",
        "cena_zweryfikowana",
    ),
    FARES: (
        "miasto",
        "typ_biletu",
        "strefa",
        "waluta",
        "cena_normalna",
        "cena_ulgowa",
        "url_zrodla",
        "data_sprawdzenia",
    ),
}
OPTIONAL_COLUMNS: Mapping[str, tuple[str, ...]] = {
    PLACES: ("google_place_id", "kuchnia", "diety"),
}

# Sheet category -> backend category plus the tags the sheet value implies.
CATEGORY_MAP: Mapping[str, tuple[PlaceCategory, tuple[PlaceTag, ...]]] = {
    "museum": (PlaceCategory.MUSEUM, ()),
    "castle_palace": (
        PlaceCategory.ATTRACTION,
        (PlaceTag.HISTORY, PlaceTag.ARCHITECTURE),
    ),
    "park_garden": (PlaceCategory.PARK, (PlaceTag.PARKS,)),
    "zoo": (PlaceCategory.ATTRACTION, (PlaceTag.ANIMALS,)),
    "viewpoint": (PlaceCategory.VIEWPOINT, (PlaceTag.VIEWS,)),
    "landmark": (PlaceCategory.ATTRACTION, ()),
    "science_center": (PlaceCategory.MUSEUM, (PlaceTag.SCIENCE,)),
    "kids_attraction": (PlaceCategory.ATTRACTION, (PlaceTag.KIDS,)),
    "nature_spot": (PlaceCategory.PARK, (PlaceTag.NATURE,)),
    "restaurant": (PlaceCategory.RESTAURANT, ()),
    "lodging": (PlaceCategory.LODGING, ()),
    "cafe": (PlaceCategory.CAFE, ()),
    "nightlife": (PlaceCategory.NIGHTLIFE, ()),
    "entertainment": (PlaceCategory.ENTERTAINMENT, ()),
    "shopping": (PlaceCategory.SHOPPING, ()),
}
# Sheet tag -> backend tags. An empty tuple is a decision, not an omission:
# the backend taxonomy has no equivalent, and the place keeps its other tags.
TAG_MAP: Mapping[str, tuple[PlaceTag, ...]] = {
    "history": (PlaceTag.HISTORY,),
    "modern_history": (PlaceTag.HISTORY,),
    "wwii": (PlaceTag.HISTORY,),
    "royal_heritage": (PlaceTag.HISTORY,),
    "military": (PlaceTag.HISTORY,),
    "jewish_heritage": (PlaceTag.HISTORY,),
    "architecture": (PlaceTag.ARCHITECTURE,),
    "art": (PlaceTag.ART,),
    "urban_art": (PlaceTag.ART,),
    "science": (PlaceTag.SCIENCE,),
    "technology": (PlaceTag.SCIENCE,),
    "transport": (),
    "nature": (PlaceTag.NATURE,),
    "botanic": (PlaceTag.NATURE, PlaceTag.PARKS),
    "animals": (PlaceTag.ANIMALS,),
    "water": (PlaceTag.WATER,),
    "outdoor_walk": (),
    "panorama": (PlaceTag.VIEWS,),
    "interactive": (),
    "kids": (PlaceTag.KIDS,),
    "playground": (PlaceTag.PLAYGROUND,),
    "music": (PlaceTag.MUSIC,),
    "religion": (PlaceTag.RELIGION,),
    "sport": (PlaceTag.SPORT,),
    "family": (PlaceTag.FAMILY,),
    "nightlife": (PlaceTag.NIGHTLIFE,),
    "local_food": (PlaceTag.LOCAL_FOOD,),
    "street_food": (PlaceTag.STREET_FOOD,),
    "markets": (PlaceTag.MARKETS,),
    "shopping": (PlaceTag.SHOPPING,),
    "relaxation": (PlaceTag.RELAXATION,),
    "wellness": (PlaceTag.WELLNESS,),
    "adventure": (PlaceTag.ADVENTURE,),
    "beaches": (PlaceTag.BEACHES,),
    "cycling": (PlaceTag.CYCLING,),
    "views": (PlaceTag.VIEWS,),
}
# Sheet ticket type -> code stored in ``transit_fares.ticket_type``.
FARE_TYPES: Mapping[str, str] = {
    "jednorazowy": "single",
    "dobowy": "24h",
    "rodzinny": "family",
    "krótki": "short",
    "wielokrotny": "multi",
    "48h": "48h",
    "72h": "72h",
    "weekendowy": "weekend",
    "karta miejska": "city_card",
    "lotnisko": "airport",
}
COUNTRIES: Mapping[str, str] = {"Polska": "PL", "Niemcy": "DE"}
LODGING_AMENITIES: Mapping[str, Amenity] = {
    "basen": Amenity.POOL,
    "kuchnia": Amenity.KITCHEN,
    "parking": Amenity.PARKING,
    "pokoj_rodzinny": Amenity.FAMILY_ROOM,
    "dostepnosc_wozek": Amenity.WHEELCHAIR_ACCESSIBLE,
}
PLACE_PRICES: Mapping[str, TicketCategory] = {
    "cena_normalna": TicketCategory.ADULT,
    "cena_ulgowa": TicketCategory.REDUCED,
    "cena_dziecko": TicketCategory.CHILD,
    "cena_senior": TicketCategory.SENIOR,
    "cena_student": TicketCategory.STUDENT,
    "cena_rodzinna": TicketCategory.FAMILY,
}

_BBOX_PADDING_DEG = 0.02
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_SLUG = re.compile(r"^[a-z0-9-]+$")
_KEY = re.compile(r"^[a-z0-9-]+:\S.*$")
_MAX_KEY = 200
_MAX_NAME = 200
_MONEY = Decimal("0.01")
_MAX_LISTED_ERRORS = 40
_DEFAULT_VERIFIED_AT = time(12, 0)


@dataclass(frozen=True, slots=True)
class SheetRow:
    """One data row: its number in the sheet (header = 1) and its cells."""

    number: int
    cells: Mapping[str, Cell]


@dataclass(frozen=True, slots=True)
class Sheet:
    """One worksheet: its header and data rows."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[SheetRow, ...]


@dataclass(frozen=True, slots=True)
class RowError:
    """A problem in one row (``number`` 0 for the whole sheet)."""

    sheet: str
    number: int
    message: str

    @override
    def __str__(self) -> str:
        where = f"{self.sheet}, wiersz {self.number}" if self.number else self.sheet
        return f"{where}: {self.message}"


class SheetImportError(Exception):
    """The workbook does not match the contract; nothing was imported."""

    def __init__(self, errors: Sequence[RowError]) -> None:
        """Keep the full error list; the message shows the first ones.

        Args:
            errors: Every problem found.
        """
        self.errors = tuple(errors)
        shown = "\n".join(f"  {e}" for e in self.errors[:_MAX_LISTED_ERRORS])
        more = len(self.errors) - _MAX_LISTED_ERRORS
        tail = f"\n  ... and {more} more" if more > 0 else ""
        super().__init__(
            f"{len(self.errors)} problem(s) in the city sheet:\n{shown}{tail}"
        )


class _RowProblem(Exception):  # ruff: ignore[error-suffix-on-exception-name]  # internal control flow, not a public error
    """A single invalid cell value; caught per row."""


@dataclass(frozen=True, slots=True)
class CityValues:
    """A ``cities`` row."""

    slug: str
    name: str
    country: str
    timezone: str
    currency: str
    center_lat: float
    center_lon: float
    bbox_south: float
    bbox_west: float
    bbox_north: float
    bbox_east: float


@dataclass(frozen=True, slots=True)
class PriceValues:
    """A ``place_prices`` row (the place is attached by the importer)."""

    ticket_category: str
    unit: str
    amount: Decimal
    currency: str
    source_url: str
    verified: bool
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class PlaceValues:
    """A ``places`` row with ``source=sheet`` and its prices."""

    source_key: str
    city_slug: str
    name: str
    category: str
    tags: tuple[str, ...]
    lat: float
    lon: float
    osm_type: str | None
    osm_id: int | None
    google_place_id: str | None
    opening_hours: OpeningHours | None
    hours_source_url: str | None
    hours_verified: bool
    hours_checked_at: datetime | None
    typical_visit_min: int | None
    stairs: float | None
    wheelchair: bool | None
    indoor: bool | None
    iconic: bool
    cuisine: str | None
    diet_tags: tuple[str, ...]
    amenities: tuple[str, ...]
    prices: tuple[PriceValues, ...]


@dataclass(frozen=True, slots=True)
class FareValues:
    """A ``transit_fares`` row."""

    city_slug: str
    ticket_type: str
    person_category: str
    amount: Decimal
    currency: str
    source_url: str
    verified: bool
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class Catalog:
    """Everything the sheet says, validated."""

    cities: tuple[CityValues, ...]
    places: tuple[PlaceValues, ...]
    fares: tuple[FareValues, ...]
    warnings: tuple[str, ...] = field(default=())


class _Reader:
    """Typed access to the cells of one row; raises ``_RowProblem``."""

    def __init__(self, cells: Mapping[str, Cell]) -> None:
        self.cells = cells

    def text(self, column: str) -> str | None:
        value = self.cells.get(column)
        if value is None:
            return None
        text = f"{value:g}" if isinstance(value, float) else str(value)
        return text.strip() or None

    def required(self, column: str) -> str:
        value = self.text(column)
        if value is None:
            msg = f"brak wartości w kolumnie {column}"
            raise _RowProblem(msg)
        return value

    def number(self, column: str) -> float | None:
        value = self.cells.get(column)
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, bool | date):
            msg = f"{column}: oczekiwano liczby, jest {value!r}"
            raise _RowProblem(msg)
        if isinstance(value, str):
            try:
                return float(value.replace(",", "."))
            except ValueError:
                msg = f"{column}: oczekiwano liczby, jest {value!r}"
                raise _RowProblem(msg) from None
        return float(value)

    def integer(self, column: str) -> int | None:
        value = self.number(column)
        if value is None:
            return None
        if not value.is_integer() or value < 0:
            msg = f"{column}: oczekiwano nieujemnej liczby całkowitej, jest {value!r}"
            raise _RowProblem(msg)
        return int(value)

    def money(self, column: str) -> Decimal | None:
        value = self.number(column)
        if value is None:
            return None
        if value < 0:
            msg = f"{column}: cena nie może być ujemna ({value})"
            raise _RowProblem(msg)
        return Decimal(str(value)).quantize(_MONEY)

    def flag(self, column: str) -> bool | None:
        """Read a tak/nie cell.

        Args:
            column: Column name.

        Returns:
            True for tak, False for nie, None when the cell is empty.

        Raises:
            _RowProblem: On any other value.
        """
        value = self.text(column)
        if value is None:
            return None
        if value.lower() not in {"tak", "nie"}:
            msg = f"{column}: oczekiwano tak albo nie, jest {value!r}"
            raise _RowProblem(msg)
        return value.lower() == "tak"

    def yes_no(self, column: str) -> bool:
        return bool(self.flag(column))

    def checked_at(self) -> datetime:
        value = self.cells.get("data_sprawdzenia")
        if isinstance(value, datetime):
            day = value.date()
        elif isinstance(value, date):
            day = value
        else:
            text = self.required("data_sprawdzenia")
            try:
                day = date.fromisoformat(text)
            except ValueError:
                msg = f"data_sprawdzenia: oczekiwano RRRR-MM-DD, jest {text!r}"
                raise _RowProblem(msg) from None
        return datetime.combine(day, _DEFAULT_VERIFIED_AT, tzinfo=UTC)


def _check_header(sheet: Sheet) -> list[RowError]:
    required = REQUIRED_COLUMNS[sheet.name]
    missing = [c for c in required if c not in sheet.columns]
    errors = []
    if missing:
        errors.append(RowError(sheet.name, 0, f"brak kolumn: {', '.join(missing)}"))
    duplicated = [c for c, n in Counter(sheet.columns).items() if n > 1 and c]
    if duplicated:
        errors.append(
            RowError(sheet.name, 0, f"powtórzone kolumny: {', '.join(duplicated)}")
        )
    return errors


def _currency(reader: _Reader, expected: str) -> str:
    code = reader.required("waluta")
    if not _CURRENCY.match(code):
        msg = f"waluta: oczekiwano kodu ISO 4217, jest {code!r}"
        raise _RowProblem(msg)
    if code != expected:
        msg = f"waluta {code} różni się od waluty miasta {expected}"
        raise _RowProblem(msg)
    return code


def _parse_city(reader: _Reader) -> CityValues:
    slug = reader.required("slug")
    if not _SLUG.match(slug):
        msg = f"slug: dozwolone a-z, 0-9 i '-', jest {slug!r}"
        raise _RowProblem(msg)
    country_name = reader.required("kraj")
    country = COUNTRIES.get(country_name)
    if country is None:
        known = ", ".join(COUNTRIES)
        msg = f"kraj {country_name!r} spoza mapy ({known}); dopisz go w sheet_rows.py"
        raise _RowProblem(msg)
    zone = reader.required("strefa_czasowa")
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError, ValueError:
        msg = f"strefa_czasowa: nieznana strefa IANA {zone!r}"
        raise _RowProblem(msg) from None
    code = reader.required("waluta")
    if not _CURRENCY.match(code):
        msg = f"waluta: oczekiwano kodu ISO 4217, jest {code!r}"
        raise _RowProblem(msg)
    lat, lon = _coordinates(reader, "srodek_lat", "srodek_lon")
    return CityValues(
        slug,
        reader.required("nazwa"),
        country,
        zone,
        code,
        lat,
        lon,
        lat,
        lon,
        lat,
        lon,
    )


def _coordinates(reader: _Reader, lat_col: str, lon_col: str) -> tuple[float, float]:
    lat, lon = reader.number(lat_col), reader.number(lon_col)
    if lat is None or lon is None:
        msg = f"brak współrzędnych ({lat_col}, {lon_col})"
        raise _RowProblem(msg)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):  # ruff: ignore[magic-value-comparison]  # WGS84 bounds
        msg = f"współrzędne poza zakresem WGS84: {lat}, {lon}"
        raise _RowProblem(msg)
    return lat, lon


@dataclass(frozen=True, slots=True)
class _Identity:
    """Who a place or lodging row is: key, city, name, position, OSM object."""

    key: str
    city: CityValues
    name: str
    lat: float
    lon: float
    osm_type: str | None
    osm_id: int | None


def _identity(reader: _Reader, city_by_name: Mapping[str, CityValues]) -> _Identity:
    key = reader.required("klucz")
    if len(key) > _MAX_KEY or not _KEY.match(key):
        msg = f"klucz {key!r} ma mieć postać miasto:nazwa-slug (do {_MAX_KEY} znaków)"
        raise _RowProblem(msg)
    city_name = reader.required("miasto")
    city = city_by_name.get(city_name)
    if city is None:
        msg = f"miasto {city_name!r} nie występuje w arkuszu miasta"
        raise _RowProblem(msg)
    name = reader.required("nazwa")
    if len(name) > _MAX_NAME:
        msg = f"nazwa dłuższa niż {_MAX_NAME} znaków"
        raise _RowProblem(msg)
    coords = _coordinates(reader, "szerokosc", "dlugosc")
    osm_type, osm_id = reader.text("osm_typ"), reader.integer("osm_id")
    if (osm_type is None) != (osm_id is None):
        msg = "osm_typ i osm_id podaje się razem albo wcale"
        raise _RowProblem(msg)
    if osm_type is not None and osm_type not in {t.value for t in OsmType}:
        msg = f"osm_typ: oczekiwano node, way albo relation, jest {osm_type!r}"
        raise _RowProblem(msg)
    return _Identity(key, city, name, *coords, osm_type, osm_id)


def _hours(
    reader: _Reader,
    source_url: str,
    checked_at: datetime,
    warnings: list[str],
    where: str,
) -> tuple[OpeningHours | None, bool, str | None, datetime | None]:
    """Opening hours, their verified mark and provenance.

    Args:
        reader: The row.
        source_url: The row's source.
        checked_at: The row's check date.
        warnings: Collects hours that were left unknown on purpose.
        where: Row label for warnings.

    Returns:
        ``(opening_hours, verified, source_url, checked_at)``.

    Raises:
        _RowProblem: On unreadable hours or closure dates.
    """
    claimed = reader.yes_no("godziny_zweryfikowane")
    raw = reader.text("godziny_osm")
    if raw is None:
        if claimed:
            msg = "godziny_zweryfikowane = tak, ale godziny_osm jest puste"
            raise _RowProblem(msg)
        return None, False, None, None
    try:
        hours = parse_osm_hours(raw)
        closed = parse_closed_dates(reader.text("dni_zamkniecia") or "")
    except ValueError as exc:
        msg = f"godziny_osm {raw!r}: {exc}"
        raise _RowProblem(msg) from exc
    if hours is None:
        warnings.append(f"{where}: godziny sezonowe {raw!r} zostają nieznane")
        return None, False, None, None
    hours = OpeningHours(weekly=hours.weekly, closed_dates=closed)
    return hours, claimed, source_url, checked_at


def _prices(
    reader: _Reader, currency: str, source_url: str, checked_at: datetime
) -> tuple[PriceValues, ...]:
    verified = reader.yes_no("ceny_zweryfikowane")
    family_rule = (reader.text("zasady_rodzinnego") or "").lower()
    prices = []
    for column, category in PLACE_PRICES.items():
        amount = reader.money(column)
        if amount is None:
            continue
        unit = (
            PriceUnit.PERSON
            if category == TicketCategory.FAMILY and family_rule.startswith("od osoby")
            else PriceUnit.GROUP
            if category == TicketCategory.FAMILY
            else PriceUnit.PERSON
        )
        prices.append(
            PriceValues(
                category.value,
                unit.value,
                amount,
                currency,
                source_url,
                verified,
                checked_at,
            )
        )
    return tuple(prices)


def _enum_list(
    reader: _Reader, column: str, allowed: type[Cuisine | DietTag]
) -> list[str]:
    values = [v.strip() for v in (reader.text(column) or "").split(",") if v.strip()]
    valid = {m.value for m in allowed}
    unknown = [v for v in values if v not in valid]
    if unknown:
        msg = f"{column}: wartości spoza enuma ({', '.join(unknown)})"
        raise _RowProblem(msg)
    return values


def _category_and_tags(reader: _Reader) -> tuple[str, tuple[str, ...]]:
    sheet_category = reader.required("kategoria")
    mapped = CATEGORY_MAP.get(sheet_category)
    if mapped is None:
        msg = f"kategoria {sheet_category!r} spoza mapy w sheet_rows.py"
        raise _RowProblem(msg)
    category, implied = mapped
    raw = [t.strip() for t in (reader.text("tagi") or "").split(",") if t.strip()]
    unknown = [t for t in raw if t not in TAG_MAP]
    if unknown:
        msg = f"tagi spoza mapy w sheet_rows.py: {', '.join(unknown)}"
        raise _RowProblem(msg)
    tags: list[PlaceTag] = list(implied)
    for tag in raw:
        tags.extend(TAG_MAP[tag])
    if reader.yes_no("dla_dzieci"):
        tags.append(PlaceTag.KIDS)
    return category.value, tuple(dict.fromkeys(t.value for t in tags))


def _accessibility(reader: _Reader) -> tuple[float | None, bool | None]:
    """Stairs share and wheelchair access; empty cells stay unknown.

    Args:
        reader: The row.

    Returns:
        ``(stairs, wheelchair)``; "częściowo" is neither yes nor no: unknown.

    Raises:
        _RowProblem: On a value outside the contract.
    """
    stairs = reader.number("schody")
    if stairs is not None and not 0 <= stairs <= 1:
        msg = f"schody: oczekiwano liczby od 0 do 1, jest {stairs}"
        raise _RowProblem(msg)
    wheelchair = reader.text("wozek")
    if wheelchair not in {None, "tak", "częściowo", "nie"}:
        msg = f"wozek: oczekiwano tak, częściowo, nie albo pusto, jest {wheelchair!r}"
        raise _RowProblem(msg)
    return stairs, {"tak": True, "nie": False}.get(wheelchair or "")


def _parse_place(
    reader: _Reader, city_by_name: Mapping[str, CityValues], warnings: list[str]
) -> PlaceValues:
    who = _identity(reader, city_by_name)
    source_url, checked_at = reader.required("url_zrodla"), reader.checked_at()
    category, tags = _category_and_tags(reader)
    hours, hours_verified, hours_source, hours_checked = _hours(
        reader, source_url, checked_at, warnings, who.key
    )
    prices = _prices(reader, _price_currency(reader, who.city), source_url, checked_at)
    stairs, wheelchair = _accessibility(reader)
    cuisines = _enum_list(reader, "kuchnia", Cuisine)
    if len(cuisines) > 1:
        msg = "kuchnia: tylko jedna wartość"
        raise _RowProblem(msg)
    return PlaceValues(
        source_key=who.key,
        city_slug=who.city.slug,
        name=who.name,
        category=category,
        tags=tags,
        lat=who.lat,
        lon=who.lon,
        osm_type=who.osm_type,
        osm_id=who.osm_id,
        google_place_id=reader.text("google_place_id"),
        opening_hours=hours,
        hours_source_url=hours_source,
        hours_verified=hours_verified,
        hours_checked_at=hours_checked,
        typical_visit_min=reader.integer("czas_wizyty_min"),
        stairs=stairs,
        # "częściowo" is neither accessible nor not: stays unknown.
        wheelchair=wheelchair,
        indoor=reader.flag("kryte"),
        iconic=reader.yes_no("kultowe"),
        cuisine=cuisines[0] if cuisines else None,
        diet_tags=tuple(_enum_list(reader, "diety", DietTag)),
        amenities=(),
        prices=prices,
    )


def _price_currency(reader: _Reader, city: CityValues) -> str:
    """Currency of the row, checked against the city's.

    Args:
        reader: The row.
        city: The row's city.

    Returns:
        The currency code; the city's when the row has no prices.
    """
    has_price = any(reader.number(c) is not None for c in PLACE_PRICES)
    return _currency(reader, city.currency) if has_price else city.currency


def _three_state(reader: _Reader, column: str) -> bool | None:
    value = reader.text(column)
    if value not in {"tak", "nie", "niepotwierdzone"}:
        msg = f"{column}: oczekiwano tak, nie albo niepotwierdzone, jest {value!r}"
        raise _RowProblem(msg)
    return {"tak": True, "nie": False}.get(value)


def _parse_lodging(
    reader: _Reader, city_by_name: Mapping[str, CityValues]
) -> PlaceValues:
    who = _identity(reader, city_by_name)
    source_url, checked_at = reader.required("url_zrodla"), reader.checked_at()
    kind = reader.required("typ")
    if kind not in {"standard", "special"}:
        msg = f"typ: oczekiwano standard albo special, jest {kind!r}"
        raise _RowProblem(msg)
    states = {column: _three_state(reader, column) for column in LODGING_AMENITIES}
    amount = reader.money("cena_za_noc_od")
    verified = reader.yes_no("cena_zweryfikowana")
    prices = ()
    if amount is not None:
        prices = (
            PriceValues(
                TicketCategory.ADULT.value,
                PriceUnit.NIGHT.value,
                amount,
                _currency(reader, who.city.currency),
                source_url,
                verified,
                checked_at,
            ),
        )
    return PlaceValues(
        source_key=who.key,
        city_slug=who.city.slug,
        name=who.name,
        category=PlaceCategory.LODGING.value,
        tags=(),
        lat=who.lat,
        lon=who.lon,
        osm_type=who.osm_type,
        osm_id=who.osm_id,
        google_place_id=None,
        opening_hours=None,
        hours_source_url=None,
        hours_verified=False,
        hours_checked_at=None,
        typical_visit_min=None,
        stairs=None,
        wheelchair=states["dostepnosc_wozek"],
        indoor=None,
        iconic=False,
        cuisine=None,
        diet_tags=(),
        # Only "tak" is a present amenity; "nie" and "niepotwierdzone" are absent.
        amenities=tuple(
            LODGING_AMENITIES[c].value for c, state in states.items() if state
        ),
        prices=prices,
    )


def _parse_fares(
    reader: _Reader, city_by_name: Mapping[str, CityValues]
) -> list[FareValues]:
    city_name = reader.required("miasto")
    city = city_by_name.get(city_name)
    if city is None:
        msg = f"miasto {city_name!r} nie występuje w arkuszu miasta"
        raise _RowProblem(msg)
    kind = reader.required("typ_biletu")
    code = FARE_TYPES.get(kind)
    if code is None:
        msg = f"typ_biletu {kind!r} spoza mapy w sheet_rows.py"
        raise _RowProblem(msg)
    source_url, checked_at = reader.required("url_zrodla"), reader.checked_at()
    zone = reader.text("strefa")
    ticket_type = f"{code}:{zone}" if zone else code
    fares = []
    for column, person in (("cena_normalna", "adult"), ("cena_ulgowa", "reduced")):
        amount = reader.money(column)
        if amount is None:
            continue  # no ticket, or the price is unconfirmed: no row
        fares.append(
            FareValues(
                city.slug,
                ticket_type,
                person,
                amount,
                _currency(reader, city.currency),
                source_url,
                # A price in the sheet comes with the source and the day it was read.
                verified=True,
                checked_at=checked_at,
            )
        )
    return fares


def _with_bbox(
    cities: Sequence[CityValues], places: Sequence[PlaceValues]
) -> tuple[CityValues, ...]:
    """Bounding box of each city: its centre and places, padded.

    Args:
        cities: Cities from the sheet.
        places: Validated places and lodgings.

    Returns:
        The cities with their box filled in.
    """
    result = []
    for city in cities:
        mine = [p for p in places if p.city_slug == city.slug]
        lats = [city.center_lat, *(p.lat for p in mine)]
        lons = [city.center_lon, *(p.lon for p in mine)]
        result.append(
            CityValues(
                city.slug,
                city.name,
                city.country,
                city.timezone,
                city.currency,
                city.center_lat,
                city.center_lon,
                round(min(lats) - _BBOX_PADDING_DEG, 6),
                round(min(lons) - _BBOX_PADDING_DEG, 6),
                round(max(lats) + _BBOX_PADDING_DEG, 6),
                round(max(lons) + _BBOX_PADDING_DEG, 6),
            )
        )
    return tuple(result)


def _duplicates(
    places: Sequence[PlaceValues], lodgings: Sequence[PlaceValues]
) -> list[RowError]:
    """Keys and OSM objects that appear twice in the workbook.

    Args:
        places: Rows of the places sheet.
        lodgings: Rows of the lodgings sheet.

    Returns:
        One error per repeated key or OSM object.
    """
    both = [*places, *lodgings]
    keys = Counter(p.source_key for p in both)
    osm = Counter((p.osm_type, p.osm_id) for p in both if p.osm_type)
    return [
        RowError(PLACES, 0, f"klucz {key!r} występuje {n} razy")
        for key, n in keys.items()
        if n > 1
    ] + [
        RowError(PLACES, 0, f"obiekt OSM {ref[0]}/{ref[1]} występuje {n} razy")
        for ref, n in osm.items()
        if n > 1
    ]


def _collect[T](
    sheet: Sheet, errors: list[RowError], parse: Callable[[_Reader], T]
) -> list[T]:
    """Parse every row of a sheet, recording the bad ones.

    Args:
        sheet: The worksheet.
        errors: Collects row problems.
        parse: Row parser; raises ``_RowProblem`` on a bad cell.

    Returns:
        The parsed rows (numbered like the sheet), without the bad ones.
    """
    parsed = []
    for row in sheet.rows:
        try:
            parsed.append(parse(_Reader(row.cells)))
        except _RowProblem as exc:
            errors.append(RowError(sheet.name, row.number, str(exc)))
    return parsed


def _check_contract(sheets: Mapping[str, Sheet]) -> list[RowError]:
    errors = [
        RowError(name, 0, "brak arkusza (zmiana nazwy łamie kontrakt importu)")
        for name in REQUIRED_COLUMNS
        if name not in sheets
    ]
    for name in REQUIRED_COLUMNS:
        if name in sheets:
            errors.extend(_check_header(sheets[name]))
    return errors


def parse_workbook(sheets: Mapping[str, Sheet]) -> Catalog:
    """Validate the whole workbook and turn it into catalog rows.

    Args:
        sheets: The worksheets by name.

    Returns:
        The validated catalog.

    Raises:
        SheetImportError: When a sheet or column is missing or any row is
            invalid; the error lists every problem with its row number.
    """
    if errors := _check_contract(sheets):
        raise SheetImportError(errors)
    warnings: list[str] = []
    cities = _collect(sheets[CITIES], errors, _parse_city)
    by_name = {c.name: c for c in cities}
    if len(by_name) != len(cities):
        errors.append(RowError(CITIES, 0, "nazwa miasta powtórzona"))
    places = _collect(
        sheets[PLACES], errors, lambda r: _parse_place(r, by_name, warnings)
    )
    lodgings = _collect(sheets[LODGINGS], errors, lambda r: _parse_lodging(r, by_name))
    fares = [
        fare
        for group in _collect(sheets[FARES], errors, lambda r: _parse_fares(r, by_name))
        for fare in group
    ]
    errors.extend(_duplicates(places, lodgings))
    fare_keys = Counter((f.city_slug, f.ticket_type, f.person_category) for f in fares)
    errors.extend(
        RowError(FARES, 0, f"powtórzony bilet {key}")
        for key, n in fare_keys.items()
        if n > 1
    )
    if errors:
        raise SheetImportError(errors)
    catalog_places = (*places, *lodgings)
    return Catalog(
        _with_bbox(cities, catalog_places),
        catalog_places,
        tuple(fares),
        tuple(warnings),
    )
