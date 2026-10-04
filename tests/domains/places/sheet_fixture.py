"""A small valid city workbook, with overrides to break it one cell at a time.

``python -m tests.domains.places.sheet_fixture`` rewrites the committed
``data/miasta.xlsx`` (two cities, a handful of rows covering the sheet's cases).
"""

from collections.abc import Iterable, Mapping
from pathlib import Path

from openpyxl import Workbook

from tuttitrip.places.logic.sheet_rows import (
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
    Cell,
    Sheet,
    SheetRow,
)

DATA = Path(__file__).parent / "data" / "miasta.xlsx"
SOURCE = "https://example.test/cennik"
CHECKED = "2026-10-03"

Row = dict[str, Cell]

CITIES: list[Row] = [
    {
        "slug": "warszawa",
        "nazwa": "Warszawa",
        "kraj": "Polska",
        "strefa_czasowa": "Europe/Warsaw",
        "waluta": "PLN",
        "srodek_lat": 52.2297,
        "srodek_lon": 21.0122,
        "miasto_pokazowe": "tak",
    },
    {
        "slug": "berlin",
        "nazwa": "Berlin",
        "kraj": "Niemcy",
        "strefa_czasowa": "Europe/Berlin",
        "waluta": "EUR",
        "srodek_lat": 52.52,
        "srodek_lon": 13.405,
        "miasto_pokazowe": "nie",
    },
]


def place(**overrides: Cell) -> Row:
    """A verified museum in Warszawa; override cells to change it."""
    row: Row = {
        "klucz": "warszawa:muzeum",
        "miasto": "Warszawa",
        "nazwa": "Muzeum",
        "szerokosc": 52.23,
        "dlugosc": 21.0,
        "osm_typ": "way",
        "osm_id": 1001.0,
        "kategoria": "museum",
        "tagi": "history,wwii",
        "godziny_osm": "Tu-Su 10:00-18:00; Mo off",
        "dni_zamkniecia": "poniedziałek; 2026-12-24",
        "czas_wizyty_min": 90.0,
        "kryte": "tak",
        "kultowe": "nie",
        "dla_dzieci": "tak",
        "waluta": "PLN",
        "cena_normalna": 35.0,
        "cena_ulgowa": 25.0,
        "cena_dziecko": None,
        "cena_senior": 20.5,
        "cena_student": None,
        "cena_rodzinna": 80.0,
        "zasady_rodzinnego": "2 dorosłych + 2 dzieci",
        "wozek": "tak",
        "schody": None,
        "url_zrodla": SOURCE,
        "data_sprawdzenia": CHECKED,
        "godziny_zweryfikowane": "tak",
        "ceny_zweryfikowane": "tak",
        "uwagi": "Czwartki bezpłatnie.",
    }
    return row | overrides


def lodging(**overrides: Cell) -> Row:
    """A standard lodging in Warszawa with a pool and unconfirmed rest."""
    row: Row = {
        "klucz": "warszawa:hotel",
        "miasto": "Warszawa",
        "nazwa": "Hotel",
        "szerokosc": 52.24,
        "dlugosc": 21.01,
        "osm_typ": "node",
        "osm_id": 2002.0,
        "typ": "standard",
        "cena_za_noc_od": None,
        "waluta": "PLN",
        "basen": "tak",
        "kuchnia": "niepotwierdzone",
        "parking": "nie",
        "pokoj_rodzinny": "niepotwierdzone",
        "dostepnosc_wozek": "niepotwierdzone",
        "url_zrodla": SOURCE,
        "data_sprawdzenia": CHECKED,
        "cena_zweryfikowana": "nie",
    }
    return row | overrides


def fare(**overrides: Cell) -> Row:
    """A single ticket in Warszawa."""
    row: Row = {
        "miasto": "Warszawa",
        "operator": "ZTM",
        "typ_biletu": "jednorazowy",
        "opis": "bilet 75-minutowy",
        "strefa": "1",
        "waluta": "PLN",
        "cena_normalna": 4.4,
        "cena_ulgowa": 2.2,
        "url_zrodla": SOURCE,
        "data_sprawdzenia": CHECKED,
    }
    return row | overrides


def default_sheets() -> dict[str, list[Row]]:
    """Rows per sheet name: two cities and one of each kind of row."""
    return {
        "miasta": CITIES,
        "miejsca": [
            place(),
            place(
                klucz="warszawa:park",
                nazwa="Park",
                osm_id=1002.0,
                kategoria="park_garden",
                tagi="outdoor_walk,nature",
                godziny_osm="24/7",
                kryte="nie",
                cena_normalna=0.0,
                cena_ulgowa=None,
                cena_senior=None,
                cena_rodzinna=None,
                godziny_zweryfikowane="nie",
                ceny_zweryfikowane="nie",
                dla_dzieci="nie",
            ),
            place(
                klucz="warszawa:zoo",
                nazwa="Zoo",
                osm_id=1003.0,
                kategoria="zoo",
                tagi="animals",
                godziny_osm="Jan-Feb 09:00-16:00; Mar-Dec 09:00-18:00",
                godziny_zweryfikowane="tak",
                cena_normalna=None,
                cena_ulgowa=None,
                cena_senior=None,
                cena_rodzinna=None,
                ceny_zweryfikowane="nie",
            ),
            place(
                klucz="warszawa:bar",
                nazwa="Bar mleczny",
                osm_typ=None,
                osm_id=None,
                kategoria="restaurant",
                tagi="local_food",
                godziny_osm=None,
                dni_zamkniecia=None,
                godziny_zweryfikowane="nie",
                cena_normalna=None,
                cena_ulgowa=None,
                cena_senior=None,
                cena_rodzinna=None,
                ceny_zweryfikowane="nie",
                kuchnia="polish",
                diety="vegetarian,vegan",
                kryte=None,
                wozek="częściowo",
            ),
            place(
                klucz="berlin:museum",
                miasto="Berlin",
                nazwa="Museum",
                szerokosc=52.52,
                dlugosc=13.4,
                osm_id=1004.0,
                waluta="EUR",
                cena_normalna=12.0,
                cena_ulgowa=None,
                cena_senior=None,
                cena_rodzinna=None,
            ),
        ],
        "noclegi": [
            lodging(),
            lodging(
                klucz="berlin:palace",
                miasto="Berlin",
                nazwa="Palace hotel",
                szerokosc=52.5,
                dlugosc=13.38,
                osm_id=2003.0,
                typ="special",
                cena_za_noc_od=180.0,
                waluta="EUR",
                parking="tak",
                cena_zweryfikowana="tak",
            ),
        ],
        "komunikacja": [
            fare(),
            fare(typ_biletu="dobowy", cena_normalna=15.0, cena_ulgowa=7.5),
            fare(
                typ_biletu="rodzinny",
                strefa="1+2",
                cena_normalna=40.0,
                cena_ulgowa=None,
            ),
            fare(
                typ_biletu="lotnisko", strefa=None, cena_normalna=None, cena_ulgowa=None
            ),
            fare(miasto="Berlin", waluta="EUR", cena_normalna=3.5, cena_ulgowa=None),
        ],
        "slownik": [{"rodzaj": "kategoria", "kod": "museum"}],
    }


def columns(name: str) -> tuple[str, ...]:
    """Header of a sheet: the contract's columns plus the optional ones."""
    extra = {
        "miejsca": ("uwagi", *OPTIONAL_COLUMNS["miejsca"]),
        "miasta": ("miasto_pokazowe",),
        "noclegi": ("uwagi",),
        "komunikacja": ("operator", "opis"),
    }.get(name, ())
    return tuple(
        dict.fromkeys((*REQUIRED_COLUMNS.get(name, ("rodzaj", "kod")), *extra))
    )


def sheets(
    data: Mapping[str, Iterable[Row]] | None = None,
    *,
    drop_columns: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Sheet]:
    """In-memory sheets (rows numbered from 2), optionally without columns."""
    result = {}
    for name, rows in (data or default_sheets()).items():
        dropped = set((drop_columns or {}).get(name, ()))
        header = tuple(c for c in columns(name) if c not in dropped)
        result[name] = Sheet(
            name,
            header,
            tuple(
                SheetRow(i, {k: v for k, v in row.items() if k not in dropped})
                for i, row in enumerate(rows, start=2)
            ),
        )
    return result


def write_xlsx(
    path: Path,
    data: Mapping[str, Iterable[Row]] | None = None,
    *,
    only: Iterable[str] | None = None,
) -> Path:
    """Write the workbook the way the importer expects to find it."""
    workbook = Workbook()
    workbook.remove(workbook["Sheet"])  # the default empty sheet
    for name, sheet in sheets(data).items():
        if only is not None and name not in only:
            continue
        worksheet = workbook.create_sheet(name)
        worksheet.append(list(sheet.columns))
        for row in sheet.rows:
            worksheet.append([row.cells.get(c) for c in sheet.columns])
    workbook.save(path)
    return path


if __name__ == "__main__":
    write_xlsx(DATA)
