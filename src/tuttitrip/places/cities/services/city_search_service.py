"""City suggestions: the catalog first, then what the geocoder knows."""

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places import db
from tuttitrip.places.cities.logic import suggest
from tuttitrip.places.cities.schemas import (
    CitySearchQuery,
    CitySuggestion,
    CitySuggestionPage,
)
from tuttitrip.places.cities.services.photon import PhotonGeocoder
from tuttitrip.shared.pagination.schemas import Page


async def search_cities(
    session: AsyncSession, geocoder: PhotonGeocoder, query: CitySearchQuery
) -> CitySuggestionPage:
    """Suggest cities for what the user typed.

    Catalog cities that match come first, marked ready when they have places;
    geocoder hits that are not catalog cities follow. A geocoder that fails
    leaves the catalog matches and ``geocoder_available=False``.

    Args:
        session: Open session.
        geocoder: The Photon client (cached and rate-limited).
        query: Text, language and page.

    Returns:
        One page of the merged list.
    """
    rows = await db.select_cities(session)
    ready = await db.select_city_slugs_with_places(session)
    catalog = [
        suggest.CatalogCity(
            c.slug, c.name, c.country, c.center_lat, c.center_lon, c.slug in ready
        )
        for c in rows
    ]
    hits = await geocoder.search(query.q, query.lang)
    merged = suggest.merge(catalog, suggest.match_catalog(catalog, query.q), hits or [])
    page = merged[query.offset : query.offset + query.size]
    base = Page[CitySuggestion].of(page, len(merged), query)
    return CitySuggestionPage(**dict(base), geocoder_available=hits is not None)
