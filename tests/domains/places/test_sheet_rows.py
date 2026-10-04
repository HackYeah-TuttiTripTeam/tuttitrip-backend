"""The city sheet parser: contract, provenance, unknown stays unknown."""

from decimal import Decimal
from pathlib import Path

import pytest

from tests.domains.places import sheet_fixture as fx
from tuttitrip.places.logic.sheet_rows import (
    Catalog,
    PlaceValues,
    SheetImportError,
    parse_workbook,
)
from tuttitrip.places.services import sheet_reader


def parse(**kwargs: list[fx.Row]) -> Catalog:
    data = fx.default_sheets() | kwargs
    return parse_workbook(fx.sheets(data))


def errors(**kwargs: list[fx.Row]) -> list[str]:
    with pytest.raises(SheetImportError) as caught:
        parse(**kwargs)
    return [str(e) for e in caught.value.errors]


def only_place(**overrides: fx.Cell) -> PlaceValues:
    catalog = parse(miejsca=[fx.place(**overrides)])
    return next(p for p in catalog.places if p.category != "lodging")


def test_committed_sample_workbook_parses_without_network() -> None:
    catalog = parse_workbook(sheet_reader.read_workbook(fx.DATA))
    assert [c.slug for c in catalog.cities] == ["warszawa", "berlin"]
    assert len(catalog.places) == 7  # 5 places + 2 lodgings
    assert len(catalog.fares) == 6


def test_a_verified_place_keeps_prices_hours_and_provenance() -> None:
    place = only_place()
    assert (place.category, place.indoor, place.wheelchair) == ("museum", True, True)
    assert place.tags == ("history", "kids")
    assert place.hours_verified
    assert place.opening_hours is not None
    assert [d.isoformat() for d in place.opening_hours.closed_dates] == ["2026-12-24"]
    prices = {p.ticket_category: p for p in place.prices}
    assert set(prices) == {"adult", "reduced", "senior", "family"}
    assert prices["senior"].amount == Decimal("20.50")
    assert prices["family"].unit == "group"
    assert all(p.verified and p.source_url == fx.SOURCE for p in prices.values())
    assert place.hours_checked_at is not None
    assert place.hours_checked_at.date().isoformat() == fx.CHECKED


def test_family_price_per_person_when_the_rules_say_so() -> None:
    place = only_place(zasady_rodzinnego="od osoby, min. 3 osoby")
    assert {p.ticket_category: p.unit for p in place.prices}["family"] == "person"


def test_empty_prices_and_hours_stay_unknown() -> None:
    place = only_place(
        cena_normalna=None,
        cena_ulgowa=None,
        cena_senior=None,
        cena_rodzinna=None,
        godziny_osm=None,
        godziny_zweryfikowane="nie",
        ceny_zweryfikowane="nie",
    )
    assert place.prices == ()
    assert place.opening_hours is None
    assert place.hours_source_url is None
    assert not place.hours_verified


def test_free_admission_is_a_zero_price_not_a_missing_one() -> None:
    park = next(p for p in parse().places if p.source_key == "warszawa:park")
    assert [(p.ticket_category, p.amount, p.verified) for p in park.prices] == [
        ("adult", Decimal("0.00"), False)
    ]
    assert park.opening_hours is not None  # 24/7
    assert not park.hours_verified


def test_seasonal_hours_become_unknown_with_a_warning() -> None:
    catalog = parse()
    zoo = next(p for p in catalog.places if p.source_key == "warszawa:zoo")
    assert zoo.opening_hours is None
    assert not zoo.hours_verified
    assert any("warszawa:zoo" in w for w in catalog.warnings)


def test_unknown_flags_stay_unknown() -> None:
    bar = next(p for p in parse().places if p.source_key == "warszawa:bar")
    assert bar.indoor is None  # empty `kryte` is not "outdoors"
    assert bar.wheelchair is None  # "częściowo" is neither yes nor no
    assert bar.stairs is None
    assert (bar.cuisine, bar.diet_tags) == ("polish", ("vegetarian", "vegan"))
    assert bar.osm_type is None


def test_sheet_dictionary_maps_onto_backend_enums() -> None:
    zoo = next(p for p in parse().places if p.source_key == "warszawa:zoo")
    assert (zoo.category, zoo.tags) == ("attraction", ("animals", "kids"))
    park = next(p for p in parse().places if p.source_key == "warszawa:park")
    assert (park.category, park.tags) == ("park", ("parks", "nature"))


def test_lodging_amenities_are_three_state() -> None:
    hotel = next(p for p in parse().places if p.source_key == "warszawa:hotel")
    assert hotel.category == "lodging"
    assert hotel.amenities == ("pool",)  # "nie" and "niepotwierdzone" are absent
    assert hotel.wheelchair is None
    assert hotel.prices == ()
    palace = next(p for p in parse().places if p.source_key == "berlin:palace")
    assert [(p.unit, p.amount, p.currency, p.verified) for p in palace.prices] == [
        ("night", Decimal("180.00"), "EUR", True)
    ]


def test_fares_keep_the_zone_and_skip_unpriced_tickets() -> None:
    fares = parse().fares
    keys = {(f.city_slug, f.ticket_type, f.person_category) for f in fares}
    assert keys == {
        ("warszawa", "single:1", "adult"),
        ("warszawa", "single:1", "reduced"),
        ("warszawa", "24h:1", "adult"),
        ("warszawa", "24h:1", "reduced"),
        ("warszawa", "family:1+2", "adult"),
        ("berlin", "single:1", "adult"),
    }
    assert all(f.verified and f.source_url == fx.SOURCE for f in fares)


def test_city_box_covers_the_center_and_every_place() -> None:
    warszawa = next(c for c in parse().cities if c.slug == "warszawa")
    assert warszawa.country == "PL"
    assert warszawa.bbox_south < 52.2297 < warszawa.bbox_north
    assert warszawa.bbox_west < 21.0 < warszawa.bbox_east


@pytest.mark.parametrize("column", ["url_zrodla", "data_sprawdzenia"])
def test_a_row_without_source_or_date_is_rejected_with_its_row_number(
    column: str,
) -> None:
    rows = [fx.place(), fx.place(klucz="warszawa:b", osm_id=9.0, **{column: None})]
    assert errors(miejsca=rows) == [
        f"miejsca, wiersz 3: brak wartości w kolumnie {column}"
    ]


def test_a_malformed_date_is_rejected() -> None:
    [message] = errors(miejsca=[fx.place(data_sprawdzenia="3.10.2026")])
    assert message.startswith("miejsca, wiersz 2: data_sprawdzenia")


def test_every_bad_row_is_reported_in_one_go() -> None:
    rows = [
        fx.place(kategoria="spa"),
        fx.place(klucz="warszawa:b", osm_id=2.0, tagi="history,teleport"),
        fx.place(klucz="warszawa:c", osm_id=3.0, szerokosc=None),
        fx.place(klucz="warszawa:d", osm_id=4.0, miasto="Londyn"),
        fx.place(klucz="warszawa:e", osm_id=5.0, cena_normalna=-1.0),
        fx.place(klucz="warszawa:f", osm_id=6.0, waluta="EUR"),
    ]
    messages = errors(miejsca=rows)
    assert len(messages) == 6
    assert "kategoria 'spa' spoza mapy" in messages[0]
    assert "teleport" in messages[1]
    assert messages[2].startswith("miejsca, wiersz 4")
    assert "Londyn" in messages[3]
    assert "ujemna" in messages[4]
    assert "waluta EUR różni się od waluty miasta PLN" in messages[5]


def test_verified_hours_claim_without_hours_is_rejected() -> None:
    [message] = errors(miejsca=[fx.place(godziny_osm=None)])
    assert "godziny_zweryfikowane = tak" in message


def test_duplicate_keys_and_osm_objects_are_rejected() -> None:
    twice = errors(miejsca=[fx.place(), fx.place(nazwa="Inne")])
    assert any("występuje 2 razy" in m for m in twice)
    same_osm = errors(miejsca=[fx.place(), fx.place(klucz="warszawa:x")])
    assert any("OSM way/1001" in m for m in same_osm)


def test_osm_type_and_id_come_together() -> None:
    assert any("razem" in m for m in errors(miejsca=[fx.place(osm_id=None)]))


@pytest.mark.parametrize("hours", ["Xx 10:00-18:00", "Mo 10:00-10:00"])
def test_unreadable_hours_are_rejected(hours: str) -> None:
    [message] = errors(miejsca=[fx.place(godziny_osm=hours)])
    assert "godziny_osm" in message


def test_a_missing_sheet_is_schema_drift() -> None:
    data = {k: v for k, v in fx.default_sheets().items() if k != "komunikacja"}
    with pytest.raises(SheetImportError) as caught:
        parse_workbook(fx.sheets(data))
    assert [str(e) for e in caught.value.errors] == [
        "komunikacja: brak arkusza (zmiana nazwy łamie kontrakt importu)"
    ]


def test_missing_or_renamed_columns_are_schema_drift() -> None:
    with pytest.raises(SheetImportError) as caught:
        parse_workbook(fx.sheets(drop_columns={"miejsca": ["cena_rodzinna", "tagi"]}))
    assert [str(e) for e in caught.value.errors] == [
        "miejsca: brak kolumn: tagi, cena_rodzinna"
    ]


def test_optional_columns_may_be_absent() -> None:
    parse_workbook(
        fx.sheets(drop_columns={"miejsca": ["kuchnia", "diety", "google_place_id"]})
    )


def test_a_file_that_is_not_xlsx_is_rejected(tmp_path: Path) -> None:
    bogus = tmp_path / "miasta.xlsx"
    bogus.write_text("<html>Sign in</html>")
    with pytest.raises(SheetImportError, match="to nie jest XLSX"):
        sheet_reader.read_workbook(bogus)


def test_reading_xlsx_round_trips_cells(tmp_path: Path) -> None:
    path = fx.write_xlsx(tmp_path / "miasta.xlsx")
    sheets = sheet_reader.read_workbook(path)
    assert set(sheets) == {"miasta", "miejsca", "noclegi", "komunikacja", "slownik"}
    assert sheets["miejsca"].rows[0].number == 2
    assert parse_workbook(sheets) == parse()
