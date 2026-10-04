"""Place catalog queries on PostgreSQL."""

from collections.abc import Collection, Sequence
from dataclasses import asdict
from itertools import batched
from typing import cast
from uuid import UUID

from sqlalchemy import (
    Table,
    bindparam,
    column,
    delete,
    func,
    null,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tuttitrip.places.logic.sheet_rows import (
    CityValues,
    FareValues,
    PlaceValues,
    PriceValues,
)
from tuttitrip.places.models import City, Place, PlacePrice, TransitFare
from tuttitrip.places.schemas import PlaceSource


async def select_cities(session: AsyncSession) -> Sequence[City]:
    """List the covered cities.

    Args:
        session: Open session.

    Returns:
        Cities ordered by name.
    """
    return (await session.scalars(select(City).order_by(City.name))).all()


async def select_places(
    session: AsyncSession,
    city_slug: str,
    category: str | None,
    *,
    limit: int,
    offset: int,
) -> Sequence[Place]:
    """List one page of the places of one city.

    Args:
        session: Open session.
        city_slug: City slug.
        category: Restrict to one category, or None for all.
        limit: Page size.
        offset: Rows to skip.

    Returns:
        Places with their prices, ordered by name (then id, for stable pages).
    """
    query = (
        select(Place)
        .where(Place.city_slug == city_slug)
        .options(selectinload(Place.prices))
        .order_by(Place.name, Place.id)
        .limit(limit)
        .offset(offset)
    )
    if category is not None:
        query = query.where(Place.category == category)
    return (await session.scalars(query)).all()


async def select_place(session: AsyncSession, place_id: UUID) -> Place | None:
    """Fetch one place with its prices.

    Args:
        session: Open session.
        place_id: Place id.

    Returns:
        The place, or None.
    """
    return await session.scalar(
        select(Place).where(Place.id == place_id).options(selectinload(Place.prices))
    )


async def select_places_by_ids(
    session: AsyncSession, place_ids: Collection[UUID]
) -> Sequence[Place]:
    """Fetch several places with one query.

    Args:
        session: Open session.
        place_ids: Place ids.

    Returns:
        The places that exist (unknown ids are simply missing).
    """
    if not place_ids:
        return ()
    query = (
        select(Place).where(Place.id.in_(place_ids)).options(selectinload(Place.prices))
    )
    return (await session.scalars(query)).all()


# --- sheet import ---------------------------------------------------------------
# One transaction per import (the caller commits). Rows with source = 'sheet' are
# owned by the sheet: it creates and updates them, OSM rows are only taken over.

_BATCH = 100
_SHEET = PlaceSource.SHEET.value
_DEFAULT_VISIT_MIN = 60  # the column default, for rows the sheet leaves empty
_PLACE_COLUMNS = (
    "city_slug",
    "name",
    "category",
    "tags",
    "lat",
    "lon",
    "osm_type",
    "osm_id",
    "google_place_id",
    "opening_hours",
    "hours_source_url",
    "hours_verified",
    "hours_checked_at",
    "typical_visit_min",
    "stairs",
    "wheelchair",
    "indoor",
    "iconic",
    "cuisine",
    "diet_tags",
    "amenities",
)


def _place_row(place: PlaceValues) -> dict[str, object]:
    row = {name: getattr(place, name) for name in _PLACE_COLUMNS}
    hours = place.opening_hours
    # SQL NULL, not JSON null (which would satisfy `opening_hours IS NOT NULL`).
    row["opening_hours"] = hours.model_dump(mode="json") if hours else null()
    row["tags"] = list(place.tags)
    row["diet_tags"] = list(place.diet_tags)
    row["amenities"] = list(place.amenities)
    # Empty cells fall back to the column defaults (a NULL would be rejected).
    row["typical_visit_min"] = place.typical_visit_min or _DEFAULT_VISIT_MIN
    row["stairs"] = place.stairs or 0.0
    return {**row, "source": _SHEET, "source_key": place.source_key}


async def upsert_cities(session: AsyncSession, cities: Sequence[CityValues]) -> None:
    """Insert or update the cities of the sheet by slug.

    Args:
        session: Open session.
        cities: Validated sheet cities.
    """
    statement = insert(City).values([asdict(c) for c in cities])
    updated = {k: statement.excluded[k] for k in asdict(cities[0]) if k != "slug"}
    await session.execute(
        statement.on_conflict_do_update(index_elements=[City.slug], set_=updated)
    )


async def adopt_osm_rows(session: AsyncSession, places: Sequence[PlaceValues]) -> int:
    """Turn OSM rows for objects the sheet describes into sheet rows.

    The sheet wins over OSM for the same object (`osm_type`, `osm_id`): the row
    changes source and key here, and the upsert then overwrites its fields.

    Args:
        session: Open session.
        places: Validated sheet places.

    Returns:
        How many OSM rows were taken over.
    """
    taken = [p for p in places if p.osm_type and p.osm_id is not None]
    if not taken:
        return 0
    sheet_row = select(Place.id).where(
        Place.source == _SHEET, Place.source_key == bindparam("b_key")
    )
    table = cast(
        "Table", Place.__table__
    )  # Core: the ORM would want primary keys per row
    statement = (
        update(table)
        .where(
            table.c.osm_type == bindparam("b_type"),
            table.c.osm_id == bindparam("b_id"),
            table.c.source != _SHEET,
            ~sheet_row.exists(),
        )
        .values(source=_SHEET, source_key=bindparam("b_key"))
    )
    refs = [(p.osm_type, p.osm_id) for p in taken]
    # Rows that will change hands: OSM rows for these objects whose key is new.
    held = set(
        await session.scalars(
            select(Place.source_key).where(
                Place.source == _SHEET,
                Place.source_key.in_([p.source_key for p in taken]),
            )
        )
    )
    osm_rows = await session.execute(
        select(Place.osm_type, Place.osm_id).where(
            tuple_(Place.osm_type, Place.osm_id).in_(refs), Place.source != _SHEET
        )
    )
    adoptable = {(row.osm_type, row.osm_id) for row in osm_rows}
    count = sum(
        1
        for p in taken
        if (p.osm_type, p.osm_id) in adoptable and p.source_key not in held
    )
    await session.execute(
        statement,
        [
            {"b_type": p.osm_type, "b_id": p.osm_id, "b_key": p.source_key}
            for p in taken
        ],
    )
    return count


async def upsert_places(
    session: AsyncSession, places: Sequence[PlaceValues]
) -> dict[str, tuple[UUID, bool]]:
    """Insert or update sheet places by (`source`, `source_key`).

    `segment_km`, `transfer_min` and `queue_min` are not in the sheet and keep
    whatever the row has.

    Args:
        session: Open session.
        places: Validated sheet places and lodgings.

    Returns:
        For each source key: the place id and whether the row was inserted.
    """
    result: dict[str, tuple[UUID, bool]] = {}
    for chunk in batched(places, _BATCH, strict=False):
        statement = insert(Place).values([_place_row(p) for p in chunk])
        statement = statement.on_conflict_do_update(
            index_elements=[Place.source, Place.source_key],
            set_={name: statement.excluded[name] for name in _PLACE_COLUMNS},
        ).returning(Place.id, Place.source_key, (column("xmax") == 0).label("inserted"))
        for row in await session.execute(statement):
            result[str(row.source_key)] = (row.id, bool(row.inserted))
    return result


def _price_row(place_id: UUID, price: PriceValues) -> dict[str, object]:
    return {
        "place_id": place_id,
        "ticket_category": price.ticket_category,
        "unit": price.unit,
        "age_min": None,
        "age_max": None,
        "family_size": None,
        "amount": price.amount,
        "currency": price.currency,
        "source_url": price.source_url,
        "verified": price.verified,
        "checked_at": price.checked_at,
    }


async def sync_prices(
    session: AsyncSession,
    places: Sequence[PlaceValues],
    ids: dict[str, tuple[UUID, bool]],
) -> None:
    """Make the prices of the imported places equal the sheet's.

    A category missing from the sheet is removed: an empty cell means unknown.

    Args:
        session: Open session.
        places: Validated sheet places.
        ids: Place ids by source key, from `upsert_places`.
    """
    rows = [
        _price_row(ids[p.source_key][0], price) for p in places for price in p.prices
    ]
    for chunk in batched(rows, _BATCH, strict=False):
        statement = insert(PlacePrice).values(list(chunk))
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[PlacePrice.place_id, PlacePrice.ticket_category],
                set_={
                    name: statement.excluded[name]
                    for name in rows[0]
                    if name not in {"place_id", "ticket_category"}
                },
            )
        )
    stale = delete(PlacePrice).where(
        PlacePrice.place_id.in_([i for i, _ in ids.values()])
    )
    if rows:
        keep = [(r["place_id"], r["ticket_category"]) for r in rows]
        stale = stale.where(
            tuple_(PlacePrice.place_id, PlacePrice.ticket_category).not_in(keep)
        )
    await session.execute(stale)


async def sync_fares(
    session: AsyncSession, city_slugs: Collection[str], fares: Sequence[FareValues]
) -> None:
    """Make the transit fares of the sheet's cities equal the sheet's.

    Args:
        session: Open session.
        city_slugs: Every city in the sheet.
        fares: Validated fares.
    """
    rows = [asdict(f) for f in fares]
    for chunk in batched(rows, _BATCH, strict=False):
        statement = insert(TransitFare).values(list(chunk))
        key = ("city_slug", "ticket_type", "person_category")
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[getattr(TransitFare, k) for k in key],
                set_={k: statement.excluded[k] for k in rows[0] if k not in key},
            )
        )
    stale = delete(TransitFare).where(TransitFare.city_slug.in_(city_slugs))
    if rows:
        keep = [(f.city_slug, f.ticket_type, f.person_category) for f in fares]
        stale = stale.where(
            tuple_(
                TransitFare.city_slug,
                TransitFare.ticket_type,
                TransitFare.person_category,
            ).not_in(keep)
        )
    await session.execute(stale)


async def count_orphan_sheet_places(
    session: AsyncSession, keys: Collection[str]
) -> int:
    """Count sheet rows whose key is no longer in the sheet (they are kept).

    Args:
        session: Open session.
        keys: Every source key in the sheet.

    Returns:
        The number of such rows.
    """
    query = select(func.count()).where(
        Place.source == _SHEET, Place.source_key.not_in(keys)
    )
    return await session.scalar(query) or 0
