"""Platform of an offer from its link's domain (pure, no model).

Booking lives only on ``booking.com`` and its subdomains. Airbnb has a site
per country (``airbnb.com``, ``airbnb.pl``, ``airbnb.co.uk``) and the share
domain ``abnb.me``. Any other domain is a known non-platform: a hard platform
requirement is then ``unmet``.
"""

import re
from urllib.parse import urlsplit

from tuttitrip.accommodation.logic.keys import Platform

_AIRBNB = re.compile(r"(^|\.)(airbnb(\.(com|co))?\.[a-z]{2,3}|abnb\.me)$")
_BOOKING = re.compile(r"(^|\.)booking\.com$")


def link_host(url: str | None) -> str | None:
    """Host name of a link, lowercased and without a trailing dot.

    Args:
        url: The offer's link, or None.

    Returns:
        The host, or None when there is no link or it has no host.
    """
    if not url:
        return None
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    return host.rstrip(".") if host else None


def platform_of(host: str | None) -> Platform | None:
    """Platform a host belongs to.

    Args:
        host: Host name from :func:`link_host`.

    Returns:
        The platform, or None for any other domain (or no host).
    """
    if host is None:
        return None
    if _BOOKING.search(host):
        return Platform.BOOKING
    if _AIRBNB.search(host):
        return Platform.AIRBNB
    return None
