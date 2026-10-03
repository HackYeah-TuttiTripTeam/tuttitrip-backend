"""Search links to lodging platforms and the log of approved openings."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.accommodation import db
from tuttitrip.accommodation.logic.keys import Platform, RequirementKind
from tuttitrip.accommodation.logic.search_links import (
    SearchInput,
    build_links,
    nightly_cap,
)
from tuttitrip.accommodation.models import SearchOpening
from tuttitrip.accommodation.schemas import (
    NightlyPrice,
    OpeningQuery,
    SearchLinkParam,
    SearchLinkRead,
    SearchLinksRead,
    SearchOpeningRead,
)
from tuttitrip.accommodation.services import requirements_service
from tuttitrip.places.services import place_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.schemas import TripMembership, TripRead
from tuttitrip.trips.services import trip_service


class SearchLinksUnavailableError(Exception):
    """The trip has no overnight stay to search for (no dates, or one day)."""


class PlatformNotAllowedError(Exception):
    """The platform is not among the links the trip currently offers."""


def _allowed_platforms(
    requirements: list[tuple[RequirementKind, str, bool]],
) -> tuple[frozenset[Platform], bool]:
    """Platforms left by the hard platform requirements.

    Args:
        requirements: ``(kind, key, hard)`` of the trip's requirements.

    Returns:
        The allowed platforms and whether any were removed.
    """
    hard = {
        Platform(key)
        for kind, key, is_hard in requirements
        if kind is RequirementKind.PLATFORM and is_hard
    }
    return (frozenset(hard), True) if hard else (frozenset(Platform), False)


async def _area_and_currency(
    session: AsyncSession, trip: TripRead
) -> tuple[str | None, str | None]:
    city = None
    if trip.city_slug is not None:
        cities = await place_service.list_cities(session)
        city = next((c for c in cities if c.slug == trip.city_slug), None)
    area = city.name if city else trip.destination
    return area, trip.currency or (city.currency if city else None)


def _price(trip: TripRead, currency: str | None, nights: int) -> NightlyPrice | None:
    amount, basis = nightly_cap(trip.budget_day_max, trip.budget_total_max, nights)
    if amount is None or basis is None or currency is None:
        return None
    return NightlyPrice(amount=amount, currency=currency, basis=basis)


async def get_search_links(
    session: AsyncSession, membership: TripMembership
) -> SearchLinksRead:
    """Build the search links of a trip from its stored data (reads only).

    Args:
        session: Open session.
        membership: Proof from ``TripAccess``.

    Returns:
        The group, dates, nightly price and one link per allowed platform.

    Raises:
        SearchLinksUnavailableError: No dates, or a single-day outing.
    """
    trip = await trip_service.get_trip(session, membership)
    start, end = trip.start_date, trip.end_date
    if start is None or end is None or end <= start:
        msg = "Search links need a trip with at least one night (start and end date)"
        raise SearchLinksUnavailableError(msg)
    profiles = await profile_service.list_profiles(session, membership)
    stored = await requirements_service.get_requirements(session, membership.trip_id)
    area, currency = await _area_and_currency(session, trip)
    nights = (end - start).days
    price = _price(trip, currency, nights)
    search = SearchInput(
        check_in=start,
        check_out=end,
        ages=tuple(p.age for p in profiles),
        area=area,
        max_price_per_night=price.amount if price else None,
        currency=price.currency if price else None,
    )
    platforms, restricted = _allowed_platforms(
        [(r.kind, r.key, r.hard) for r in stored.requirements]
    )
    return SearchLinksRead(
        check_in=start,
        check_out=end,
        nights=nights,
        adults=search.adults,
        child_ages=list(search.child_ages),
        area=area,
        price_per_night=price,
        platforms_restricted=restricted,
        requirements_version=stored.version,
        links=[
            SearchLinkRead(
                platform=link.platform,
                url=link.url,
                fallback_url=link.fallback_url,
                params=[
                    SearchLinkParam.model_validate(p, from_attributes=True)
                    for p in link.params
                ],
            )
            for link in build_links(search, platforms)
        ],
    )


async def record_opening(
    session: AsyncSession, membership: TripMembership, platform: Platform
) -> SearchOpeningRead:
    """Log that the host approved opening a platform, with the link's parameters.

    The parameters are rebuilt on the server, never taken from the client.

    Args:
        session: Open session.
        membership: Proof from ``TripAccess`` (the host).
        platform: The platform the host approved.

    Returns:
        The new log entry.

    Raises:
        SearchLinksUnavailableError: The trip has no overnight stay.
        PlatformNotAllowedError: A requirement excludes this platform.
    """
    links = await get_search_links(session, membership)
    link = next((x for x in links.links if x.platform is platform), None)
    if link is None:
        msg = f"Platform '{platform}' is excluded by the trip's requirements"
        raise PlatformNotAllowedError(msg)
    opening = SearchOpening(
        trip_id=membership.trip_id,
        platform=platform,
        url=link.url,
        params=[p.model_dump() for p in link.params],
        actor_sub=membership.sub,
    )
    await db.insert_opening(session, opening)
    result = SearchOpeningRead.model_validate(opening)
    await session.commit()
    return result


async def list_openings(
    session: AsyncSession, trip_id: UUID, query: OpeningQuery
) -> Page[SearchOpeningRead]:
    """One page of the openings log.

    Args:
        session: Open session.
        trip_id: Trip id (the caller was already checked).
        query: Page, sort and filters.

    Returns:
        The page, newest first by default.
    """
    page = await db.select_openings(session, trip_id, query)
    return Page[SearchOpeningRead].of(
        [SearchOpeningRead.model_validate(row) for row in page.items],
        page.total,
        query,
    )
