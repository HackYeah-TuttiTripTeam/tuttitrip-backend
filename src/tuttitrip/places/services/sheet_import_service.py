"""Import the demo cities' sheet into the catalog (idempotent upsert).

The sheet is the source of truth for ``source = 'sheet'`` rows: the same file
imported twice changes nothing, a corrected cell updates its row. The whole
import is one transaction, so a rejected workbook leaves the catalog as it was.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places import db
from tuttitrip.places.logic.sheet_rows import Catalog, parse_workbook
from tuttitrip.places.schemas import PlaceCategory
from tuttitrip.places.services import sheet_reader

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CityCounts:
    """What the sheet holds for one city."""

    places: int
    lodgings: int
    fares: int
    prices: int
    unknown_hours: int
    unpriced: int


@dataclass(frozen=True, slots=True)
class ImportReport:
    """The outcome of one import."""

    cities: dict[str, CityCounts]
    inserted: int
    updated: int
    taken_from_osm: int
    orphans: int
    warnings: tuple[str, ...]


def summarize(catalog: Catalog) -> dict[str, CityCounts]:
    """Count the sheet's rows per city.

    Args:
        catalog: The validated sheet.

    Returns:
        Counts by city slug.
    """
    result = {}
    for city in catalog.cities:
        mine = [p for p in catalog.places if p.city_slug == city.slug]
        lodgings = [p for p in mine if p.category == PlaceCategory.LODGING]
        places = [p for p in mine if p.category != PlaceCategory.LODGING]
        result[city.slug] = CityCounts(
            places=len(places),
            lodgings=len(lodgings),
            fares=sum(1 for f in catalog.fares if f.city_slug == city.slug),
            prices=sum(len(p.prices) for p in mine),
            unknown_hours=sum(1 for p in places if p.opening_hours is None),
            unpriced=sum(1 for p in places if not p.prices),
        )
    return result


def read_catalog(path: Path) -> Catalog:
    """Read and validate a workbook without touching the database.

    Args:
        path: The XLSX file.

    Returns:
        The validated catalog.
    """
    return parse_workbook(sheet_reader.read_workbook(path))


async def import_sheet(session: AsyncSession, path: Path) -> ImportReport:
    """Validate the workbook, then upsert it in one transaction.

    Args:
        session: Open session; committed on success.
        path: The XLSX file (``miasta.xlsx``).

    Returns:
        The counts per city and the insert/update split.
    """
    catalog = read_catalog(path)
    await db.upsert_cities(session, catalog.cities)
    taken = await db.adopt_osm_rows(session, catalog.places)
    ids = await db.upsert_places(session, catalog.places)
    await db.sync_prices(session, catalog.places, ids)
    await db.sync_fares(session, [c.slug for c in catalog.cities], catalog.fares)
    orphans = await db.count_orphan_sheet_places(
        session, [p.source_key for p in catalog.places]
    )
    await session.commit()
    inserted = Counter(new for _, new in ids.values())
    return ImportReport(
        cities=summarize(catalog),
        inserted=inserted[True],
        updated=inserted[False],
        taken_from_osm=taken,
        orphans=orphans,
        warnings=catalog.warnings,
    )
