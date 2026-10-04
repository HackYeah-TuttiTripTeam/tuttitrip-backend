"""Catalog matching, Photon parsing and the merged suggestion list (pure)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tuttitrip.places.cities.logic.slug import slugify
from tuttitrip.places.cities.schemas import CitySource, CitySuggestion

MAX_SLUG_LENGTH = 64  # the trip's `city_slug` limit
COUNTRY_CODE_LENGTH = 2


@dataclass(frozen=True)
class CatalogCity:
    """A city of the catalog, with whether it has places."""

    slug: str
    name: str
    country: str
    center_lat: float
    center_lon: float
    has_places: bool


@dataclass(frozen=True)
class GeocoderHit:
    """A city found by the geocoder."""

    name: str
    country: str  # ISO 3166-1 alpha-2
    country_name: str
    region: str | None
    lat: float
    lon: float


def match_catalog(cities: Sequence[CatalogCity], query: str) -> list[CatalogCity]:
    """Pick the catalog cities that contain the query; name prefixes come first.

    Args:
        cities: The whole catalog.
        query: What the user typed.

    Returns:
        Matching cities: prefix matches by name, then the rest by name.
    """
    needle = slugify(query)
    if not needle:
        return []
    hits = [c for c in cities if needle in slugify(c.name) or needle in c.slug]
    return sorted(
        hits, key=lambda c: (not slugify(c.name).startswith(needle), slugify(c.name))
    )


def _hit(feature: Mapping[str, Any]) -> GeocoderHit | None:
    """Read one feature.

    Args:
        feature: A GeoJSON feature of Photon.

    Returns:
        The hit; None when it lacks a name, a country code or coordinates.
    """
    try:
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"][:2]
        name, code = str(props["name"]).strip(), str(props["countrycode"]).upper()
        point = float(lat), float(lon)
    except KeyError, TypeError, ValueError:
        return None
    if not name or len(code) != COUNTRY_CODE_LENGTH:
        return None
    region = props.get("state") or props.get("county")
    country_name = str(props.get("country") or code)
    return GeocoderHit(name, code, country_name, region and str(region), *point)


def parse_photon(payload: Mapping[str, Any]) -> list[GeocoderHit]:
    """Read the cities out of a Photon GeoJSON answer, skipping malformed features.

    Args:
        payload: The decoded JSON body.

    Returns:
        Hits in Photon's order.
    """
    hits = (_hit(f) for f in payload.get("features") or [])
    return [hit for hit in hits if hit is not None]


def city_query(hit: GeocoderHit) -> str:
    """Name the worker geocodes, e.g. ``"Lisboa, Portugal"``.

    Args:
        hit: A geocoder hit.

    Returns:
        ``"<name>, <country>"``.
    """
    return f"{hit.name}, {hit.country_name}"


def merge(
    catalog: Sequence[CatalogCity],
    matched: Sequence[CatalogCity],
    hits: Sequence[GeocoderHit],
) -> list[CitySuggestion]:
    """Catalog matches first, then the geocoder hits that are not catalog cities.

    A hit is a catalog city when its name and country equal the catalog's, so
    ``Gdansk`` from Photon never appears next to the catalog's ``Gdańsk``. Hits
    whose slug repeats or does not fit a `city_slug` are dropped.

    Args:
        catalog: The whole catalog (for recognising duplicates).
        matched: The catalog cities that match the query.
        hits: Geocoder hits.

    Returns:
        The suggestions in display order.
    """
    known = {(slugify(c.name), c.country) for c in catalog}
    out = [
        CitySuggestion(
            slug=c.slug,
            name=c.name,
            country=c.country,
            source=CitySource.CATALOG,
            catalog_ready=c.has_places,
            center_lat=c.center_lat,
            center_lon=c.center_lon,
        )
        for c in matched
    ]
    seen = {s.slug for s in out}
    for hit in hits:
        query = city_query(hit)
        slug = slugify(query)
        if (
            (slugify(hit.name), hit.country) in known
            or slug in seen
            or not slug
            or len(slug) > MAX_SLUG_LENGTH
        ):
            continue
        seen.add(slug)
        out.append(
            CitySuggestion(
                slug=slug,
                name=hit.name,
                country=hit.country,
                region=hit.region,
                source=CitySource.GEOCODER,
                catalog_ready=False,
                city_query=query,
                center_lat=hit.lat,
                center_lon=hit.lon,
            )
        )
    return out
