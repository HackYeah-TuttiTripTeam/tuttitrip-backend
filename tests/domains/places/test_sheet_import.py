"""Sheet import against a real PostgreSQL (migrated); local smoke step, not CI.

Run with ``uv run pytest -m integration`` after ``alembic upgrade head``. The
test city ``testville`` is created and removed by each test.
"""

import asyncio
import functools
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.domains.places import sheet_fixture as fx
from tuttitrip.places.logic.sheet_rows import SheetImportError
from tuttitrip.places.models import Place, PlacePrice, TransitFare
from tuttitrip.places.services import sheet_import_service
from tuttitrip.places.services.sheet_import_service import ImportReport
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import database_url

pytestmark = pytest.mark.integration

CITY = "testville"
INSERT_CITY = """
    INSERT INTO cities (slug, name, country, timezone, currency, center_lat,
        center_lon, bbox_south, bbox_west, bbox_north, bbox_east)
    VALUES (:c, 'T', 'PL', 'Europe/Warsaw', 'PLN', 50, 20, 49, 19, 51, 21)"""
INSERT_OSM_PLACE = """
    INSERT INTO places (city_slug, name, category, lat, lon, osm_type, osm_id, source)
    VALUES (:c, 'From OSM', 'attraction', 50, 20, 'way', :o, 'osm')"""
CREATE_WORKER_ROLE = """
    DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tuttitrip_worker') THEN
            CREATE ROLE tuttitrip_worker;
        END IF;
    END $$"""
GRANT_WORKER = (
    "GRANT SELECT, INSERT, UPDATE ON places, place_prices TO tuttitrip_worker"
)
OSM_UPSERT = """
    INSERT INTO places (city_slug, name, category, lat, lon, osm_type, osm_id, source)
    VALUES (:c, 'OSM again', 'attraction', 50, 20, 'way', :o, 'osm')
    ON CONFLICT (osm_type, osm_id)
    DO UPDATE SET name = excluded.name, source = excluded.source"""
OSM_PRICE = """
    INSERT INTO place_prices (place_id, ticket_category, amount, currency)
    SELECT id, 'student', 1, 'PLN' FROM places WHERE source_key = :k"""
OSM = 990_000_000  # far from the real sheet's OSM ids


def city_rows() -> dict[str, list[fx.Row]]:
    city: fx.Row = {
        "slug": CITY,
        "nazwa": "Testville",
        "kraj": "Polska",
        "strefa_czasowa": "Europe/Warsaw",
        "waluta": "PLN",
        "srodek_lat": 50.0,
        "srodek_lon": 20.0,
        "miasto_pokazowe": "nie",
    }
    where: fx.Row = {"miasto": "Testville"}
    return {
        "miasta": [city],
        "miejsca": [
            fx.place(klucz=f"{CITY}:a", nazwa="A", osm_id=OSM + 1, **where),
            fx.place(
                klucz=f"{CITY}:b",
                nazwa="B",
                osm_id=OSM + 2,
                godziny_osm=None,
                godziny_zweryfikowane="nie",
                cena_normalna=None,
                cena_ulgowa=None,
                cena_senior=None,
                cena_rodzinna=None,
                ceny_zweryfikowane="nie",
                **where,
            ),
        ],
        "noclegi": [fx.lodging(klucz=f"{CITY}:h", nazwa="H", osm_id=OSM + 3, **where)],
        "komunikacja": [fx.fare(**where), fx.fare(typ_biletu="dobowy", **where)],
        "slownik": [{"rodzaj": "kategoria", "kod": "museum"}],
    }


def workbook(tmp_path: Path, data: dict[str, list[fx.Row]] | None = None) -> Path:
    return fx.write_xlsx(tmp_path / "miasta.xlsx", data or city_rows())


@asynccontextmanager
async def database() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url(get_settings().database))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await clean(session)
        yield maker
    finally:
        async with maker() as session:
            await clean(session)
        await engine.dispose()


async def clean(session: AsyncSession) -> None:
    await session.execute(text("DELETE FROM places WHERE city_slug = :c"), {"c": CITY})
    await session.execute(
        text("DELETE FROM transit_fares WHERE city_slug = :c"), {"c": CITY}
    )
    await session.execute(text("DELETE FROM cities WHERE slug = :c"), {"c": CITY})
    await session.commit()


def integration(test: Callable[..., Awaitable[None]]) -> Callable[..., None]:
    def run(tmp_path: Path) -> None:
        asyncio.run(test(tmp_path))

    return functools.wraps(test)(run)


async def load(path: Path, maker: async_sessionmaker[AsyncSession]) -> ImportReport:
    async with maker() as session:
        return await sheet_import_service.import_sheet(session, path)


async def snapshot(maker: async_sessionmaker[AsyncSession]) -> list[tuple[object, ...]]:
    async with maker() as session:
        places = await session.execute(
            select(Place.source_key, Place.name, Place.opening_hours, Place.tags)
            .where(Place.city_slug == CITY)
            .order_by(Place.source_key)
        )
        prices = await session.execute(
            select(Place.source_key, PlacePrice.ticket_category, PlacePrice.amount)
            .join(PlacePrice)
            .where(Place.city_slug == CITY)
            .order_by(Place.source_key, PlacePrice.ticket_category)
        )
        fares = await session.execute(
            select(
                TransitFare.ticket_type, TransitFare.person_category, TransitFare.amount
            )
            .where(TransitFare.city_slug == CITY)
            .order_by(TransitFare.ticket_type, TransitFare.person_category)
        )
        return [tuple(r) for r in (*places, *prices, *fares)]


@integration
async def test_importing_twice_changes_nothing(tmp_path: Path) -> None:
    async with database() as maker:
        path = workbook(tmp_path)
        first = await load(path, maker)
        before = await snapshot(maker)
        second = await load(path, maker)
        assert (first.inserted, first.updated) == (3, 0)
        assert (second.inserted, second.updated) == (0, 3)
        assert await snapshot(maker) == before
        counts = first.cities[CITY]
        assert (counts.places, counts.lodgings, counts.fares) == (2, 1, 4)


@integration
async def test_a_corrected_sheet_updates_rows_and_drops_removed_prices(
    tmp_path: Path,
) -> None:
    async with database() as maker:
        await load(workbook(tmp_path), maker)
        data = city_rows()
        data["miejsca"][0] = fx.place(
            klucz=f"{CITY}:a",
            miasto="Testville",
            nazwa="A2",
            osm_id=OSM + 1,
            cena_normalna=40.0,
            cena_ulgowa=None,
            godziny_osm=None,
            godziny_zweryfikowane="nie",
        )
        data["komunikacja"] = data["komunikacja"][:1]
        await load(workbook(tmp_path, data), maker)
        async with maker() as session:
            place = await session.scalar(
                select(Place).where(Place.source_key == f"{CITY}:a")
            )
            assert place is not None
            assert place.name == "A2"
            # SQL NULL, not a JSON null: the hours are unknown.
            unknown = await session.scalar(
                text("SELECT opening_hours IS NULL FROM places WHERE source_key = :k"),
                {"k": f"{CITY}:a"},
            )
            assert unknown is True
            rows = await session.execute(
                select(PlacePrice.ticket_category, PlacePrice.amount).where(
                    PlacePrice.place_id == place.id
                )
            )
            amounts = dict(rows.all())
            assert sorted(amounts) == ["adult", "family", "senior"]
            assert amounts["adult"] == Decimal("40.00")
        fares = {r[0] for r in await snapshot(maker) if str(r[0]).endswith(":1")}
        assert fares == {"single:1"}  # the dropped 24h ticket is gone


@integration
async def test_a_rejected_sheet_leaves_the_catalog_as_it_was(tmp_path: Path) -> None:
    async with database() as maker:
        await load(workbook(tmp_path), maker)
        before = await snapshot(maker)
        data = city_rows()
        data["miejsca"][0] = fx.place(
            klucz=f"{CITY}:a", miasto="Testville", osm_id=OSM + 1, url_zrodla=None
        )
        with pytest.raises(SheetImportError, match="wiersz 2"):
            await load(workbook(tmp_path, data), maker)
        assert await snapshot(maker) == before


@integration
async def test_the_sheet_takes_over_an_osm_row_for_the_same_object(
    tmp_path: Path,
) -> None:
    async with database() as maker:
        async with maker() as session:
            await session.execute(
                text(INSERT_CITY),
                {"c": CITY},
            )
            osm_id = await session.scalar(
                text(INSERT_OSM_PLACE + " RETURNING id"),
                {"c": CITY, "o": OSM + 1},
            )
            await session.commit()
        report = await load(workbook(tmp_path), maker)
        assert report.taken_from_osm == 1
        async with maker() as session:
            rows = (
                await session.scalars(select(Place).where(Place.city_slug == CITY))
            ).all()
            adopted = next(p for p in rows if p.id == osm_id)
            assert (adopted.source, adopted.source_key, adopted.name) == (
                "sheet",
                f"{CITY}:a",
                "A",
            )
            assert len(rows) == 3


@integration
async def test_osm_import_cannot_overwrite_sheet_rows(tmp_path: Path) -> None:
    async with database() as maker:
        await load(workbook(tmp_path), maker)
        async with maker() as session:
            await session.execute(text(CREATE_WORKER_ROLE))
            await session.execute(text(GRANT_WORKER))
            await session.commit()
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE tuttitrip_worker"))
            update = await session.execute(
                text("UPDATE places SET name = 'OSM name' WHERE city_slug = :c"),
                {"c": CITY},
            )
            upsert = await session.execute(text(OSM_UPSERT), {"c": CITY, "o": OSM + 1})
            price = await session.execute(text(OSM_PRICE), {"k": f"{CITY}:a"})
            assert (update.rowcount, upsert.rowcount, price.rowcount) == (0, 0, 0)  # ty: ignore[unresolved-attribute]
        async with maker() as session:
            names = set(
                await session.scalars(select(Place.name).where(Place.city_slug == CITY))
            )
            assert names == {"A", "B", "H"}


@integration
async def test_osm_import_still_writes_its_own_rows(tmp_path: Path) -> None:
    async with database() as maker:
        await load(workbook(tmp_path), maker)
        async with maker() as session:
            await session.execute(
                text(
                    INSERT_OSM_PLACE.replace("From OSM", "OSM").replace(
                        "'way'", "'node'"
                    )
                ),
                {"c": CITY, "o": OSM + 50},
            )
            await session.commit()
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE tuttitrip_worker"))
            await session.execute(
                text("UPDATE places SET name = 'OSM v2' WHERE osm_id = :o"),
                {"o": OSM + 50},
            )
        async with maker() as session:
            name = await session.scalar(
                select(Place.name).where(Place.osm_id == OSM + 50)
            )
            assert name == "OSM v2"


@integration
async def test_rows_missing_from_the_sheet_are_kept_and_reported(
    tmp_path: Path,
) -> None:
    async with database() as maker:
        await load(workbook(tmp_path), maker)
        data = city_rows()
        data["miejsca"] = data["miejsca"][:1]
        report = await load(workbook(tmp_path, data), maker)
        assert report.orphans >= 1
        async with maker() as session:
            kept = await session.scalar(
                select(Place.id).where(Place.source_key == f"{CITY}:b")
            )
            assert kept is not None
