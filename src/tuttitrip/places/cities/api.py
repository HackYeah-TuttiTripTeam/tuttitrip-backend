"""City search endpoint (``/places/cities/search``)."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from tuttitrip.places.cities.schemas import CitySearchQuery, CitySuggestionPage
from tuttitrip.places.cities.services import city_search_service
from tuttitrip.places.cities.services.photon import PhotonGeocoder
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/places/cities", tags=["places"])


@lru_cache(maxsize=1)
def get_geocoder() -> PhotonGeocoder:
    """Provide the process-wide geocoder (its cache and request budget are shared).

    Returns:
        The Photon client (tests override this dependency).
    """
    return PhotonGeocoder(get_settings().geocoder)


@router.get(
    "/search",
    summary="City suggestions while typing",
    description=(
        "Catalog cities that match `q` come first (`catalog_ready` says whether "
        "they have places), then OpenStreetMap cities from the Photon geocoder "
        "(`source=geocoder`, with the `city_query` to pass to "
        "`POST /trips/{id}/places/candidates`). When the geocoder is down, slow "
        "or over its request budget the answer is 200 with catalog cities only "
        "and `geocoder_available=false`. The geocoder has no offset: the list is "
        "capped by `TUTTITRIP_GEOCODER__MAX_RESULTS`, `page`/`size` slice it."
    ),
    dependencies=[requires(Feature.PLACES_CATALOG, Access.READ)],
)
async def search_cities(
    session: SessionDep,
    geocoder: Annotated[PhotonGeocoder, Depends(get_geocoder)],
    query: Annotated[CitySearchQuery, Query()],
) -> CitySuggestionPage:
    """Suggest cities for what the user typed.

    Args:
        session: Database session.
        geocoder: The Photon client.
        query: `q`, `lang` and paging.

    Returns:
        One page of suggestions, catalog cities first.
    """
    return await city_search_service.search_cities(session, geocoder, query)
