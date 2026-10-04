"""Turn a free-text destination into a city slug (pure)."""

from collections.abc import Iterable

from tuttitrip.places.candidates.logic.slug import slugify

MAX_SLUG_LENGTH = 64  # the trip's `city_slug` limit

PARTS_SEPARATOR = ","  # "Gdańsk, Polska" names the city before the first comma


def match_catalog_city(
    destination: str, catalog: Iterable[tuple[str, str]]
) -> str | None:
    """Find the catalog city a destination names.

    The match ignores case and diacritics and accepts the city name or its slug,
    also when a country or region follows a comma.

    Args:
        destination: The trip's free-text destination, e.g. ``"Kraków"``.
        catalog: ``(slug, name)`` of every catalog city.

    Returns:
        The slug of the matching city, or None.
    """
    wanted = {
        key
        for part in (destination, destination.split(PARTS_SEPARATOR, maxsplit=1)[0])
        if (key := slugify(part))
    }
    for slug, name in catalog:
        if slug in wanted or slugify(name) in wanted:
            return slug
    return None


def city_slug_for(destination: str, catalog: Iterable[tuple[str, str]]) -> str | None:
    """The slug for any destination: the catalog city's, else ``slugify``.

    A city outside the catalog still gets its slug (the same rule as the
    candidate fetch), so the plan can ask for its places.

    Args:
        destination: The trip's free-text destination.
        catalog: ``(slug, name)`` of every catalog city.

    Returns:
        The slug, or None when the text has no letters or the slug is too long.
    """
    slug = match_catalog_city(destination, catalog) or slugify(destination)
    return slug if slug and len(slug) <= MAX_SLUG_LENGTH else None
