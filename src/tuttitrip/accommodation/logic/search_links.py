"""Search links to lodging platforms, built from trip data (pure, deterministic).

TuttiTrip fetches nothing from the platforms (scraping is out of scope): a host
opens the link and pastes offers back. Neither platform documents its search
URL, so every parameter carries an ``official`` flag and each link has a
``fallback_url`` without filters. When a platform renames a parameter, only the
filter breaks, the fallback still opens the right place.

Parameters seen in the platforms' own documentation or links
(``official=True``) and parameters known only from third-party tools and from
reading live URLs (``official=False``):

- Booking: ``checkin``, ``checkout``, ``group_adults``, ``no_rooms`` appear in
  the example links of the Demand API docs (developers.booking.com). ``ss``
  (destination), ``group_children``, ``age`` (one per child) and the price
  filter ``nflt=price=<CUR>-min-<max>-1`` are not documented there.
- Airbnb: nothing is documented. ``checkin``, ``checkout``, ``adults``,
  ``children``, ``infants``, ``price_max`` and ``currency`` come from
  third-party tools. Airbnb has no per-child age, only infants (under 2) and
  children.

Every value is percent-encoded and the hosts are constants, so no input can
change where a link points.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from typing import Literal
from urllib.parse import quote, urlencode

from tuttitrip.accommodation.logic.keys import Platform

type PriceBasis = Literal["budget_day_max", "budget_total_max_per_night"]

ADULT_FROM = 18
INFANT_BELOW = 2
PLATFORM_ORDER = (Platform.BOOKING, Platform.AIRBNB)

_BOOKING_HOME = "https://www.booking.com/"
_BOOKING_SEARCH = "https://www.booking.com/searchresults.html"
_AIRBNB_HOME = "https://www.airbnb.com/"
_AIRBNB_SEARCH = "https://www.airbnb.com/s/{area}/homes"
_AIRBNB_ANYWHERE = "https://www.airbnb.com/s/homes"


@dataclass(frozen=True, slots=True)
class SearchInput:
    """What a search needs; ``area`` and the price may be unknown."""

    check_in: date
    check_out: date
    ages: tuple[int, ...]
    area: str | None = None
    max_price_per_night: int | None = None
    currency: str | None = None

    @property
    def adults(self) -> int:
        """Adults in the group (at least 1: a platform needs one).

        Returns:
            People aged 18 and over, or 1 when the group has none.
        """
        return max(1, sum(1 for age in self.ages if age >= ADULT_FROM))

    @property
    def child_ages(self) -> tuple[int, ...]:
        """Ages of the people under 18, oldest first, so the order is stable.

        Returns:
            The ages, sorted descending.
        """
        return tuple(sorted((a for a in self.ages if a < ADULT_FROM), reverse=True))


@dataclass(frozen=True, slots=True)
class LinkParam:
    """One query parameter of a link and whether the platform documents it."""

    name: str
    value: str
    official: bool


@dataclass(frozen=True, slots=True)
class SearchLink:
    """A ready search link, its parameters and the filterless fallback."""

    platform: Platform
    url: str
    fallback_url: str
    params: tuple[LinkParam, ...]


def nightly_cap(
    day_max: Decimal | None, total_max: Decimal | None, nights: int
) -> tuple[int | None, PriceBasis | None]:
    """Price per night to filter by, from the trip budget.

    The daily limit wins; without it the total limit is split over the nights.
    Both are whole-group limits for everything, so this is an upper bound for
    lodging, not a lodging budget. The amount is rounded down to a whole unit.

    Args:
        day_max: Upper bound of the daily budget (``B_do`` per day).
        total_max: Upper bound of the total budget.
        nights: Number of nights (at least 1).

    Returns:
        The amount and its basis (``budget_day_max`` or
        ``budget_total_max_per_night``), or ``(None, None)`` without a budget.
    """
    if day_max is not None:
        return int(day_max.to_integral_value(ROUND_FLOOR)), "budget_day_max"
    if total_max is not None:
        per_night = (total_max / nights).to_integral_value(ROUND_FLOOR)
        return int(per_night), "budget_total_max_per_night"
    return None, None


def _booking(search: SearchInput) -> SearchLink:
    params = [
        LinkParam("checkin", search.check_in.isoformat(), official=True),
        LinkParam("checkout", search.check_out.isoformat(), official=True),
        LinkParam("group_adults", str(search.adults), official=True),
        LinkParam("no_rooms", "1", official=True),
    ]
    if search.area:
        params.insert(0, LinkParam("ss", search.area, official=False))
    if search.child_ages:
        params.append(
            LinkParam("group_children", str(len(search.child_ages)), official=False)
        )
        params.extend(
            LinkParam("age", str(a), official=False) for a in search.child_ages
        )
    if search.max_price_per_night and search.currency:
        currency = search.currency
        params.append(LinkParam("selected_currency", currency, official=False))
        price = f"price={currency}-min-{search.max_price_per_night}-1"
        params.append(LinkParam("nflt", price, official=False))
    fallback = (
        f"{_BOOKING_SEARCH}?{urlencode({'ss': search.area}, quote_via=quote)}"
        if search.area
        else _BOOKING_HOME
    )
    url = f"{_BOOKING_SEARCH}?{_query(params)}"
    return SearchLink(Platform.BOOKING, url, fallback, tuple(params))


def _airbnb(search: SearchInput) -> SearchLink:
    infants = sum(1 for a in search.child_ages if a < INFANT_BELOW)
    children = len(search.child_ages) - infants
    params = [
        LinkParam("checkin", search.check_in.isoformat(), official=False),
        LinkParam("checkout", search.check_out.isoformat(), official=False),
        LinkParam("adults", str(search.adults), official=False),
    ]
    if children:
        params.append(LinkParam("children", str(children), official=False))
    if infants:
        params.append(LinkParam("infants", str(infants), official=False))
    if search.max_price_per_night and search.currency:
        params += [
            LinkParam("price_max", str(search.max_price_per_night), official=False),
            LinkParam("currency", search.currency, official=False),
        ]
    base = (
        _AIRBNB_SEARCH.format(area=quote(search.area, safe=""))
        if search.area
        else _AIRBNB_ANYWHERE
    )
    fallback = base if search.area else _AIRBNB_HOME
    return SearchLink(
        Platform.AIRBNB, f"{base}?{_query(params)}", fallback, tuple(params)
    )


def _query(params: list[LinkParam]) -> str:
    return urlencode([(p.name, p.value) for p in params], quote_via=quote)


def build_links(
    search: SearchInput, platforms: frozenset[Platform]
) -> list[SearchLink]:
    """Build one link per allowed platform, always in the same order.

    Args:
        search: Dates, group, area and nightly cap.
        platforms: Platforms the trip allows (never empty).

    Returns:
        Booking first, then Airbnb; identical input gives identical links.
    """
    builders = {Platform.BOOKING: _booking, Platform.AIRBNB: _airbnb}
    return [builders[p](search) for p in PLATFORM_ORDER if p in platforms]
