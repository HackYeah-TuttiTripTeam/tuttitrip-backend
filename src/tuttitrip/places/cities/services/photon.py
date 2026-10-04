"""Photon client: a cached, rate-limited city search (best effort).

Photon is a fair-use public service built on OpenStreetMap data. Answers are
cached, every request names the app and the request rate is capped, so typing
never turns into a flood. Any failure yields ``None``: the caller still has
the catalog.
"""

import logging
import time
from collections import deque
from collections.abc import Callable

import httpx

from tuttitrip.places.candidates.logic.slug import slugify
from tuttitrip.places.cities.logic.suggest import GeocoderHit, parse_photon
from tuttitrip.places.cities.schemas import CityLang
from tuttitrip.shared.config.settings import GeocoderSettings

logger = logging.getLogger(__name__)

# Photon knows default (the local names), de, en and fr; Polish gets the local names.
_PHOTON_LANG = {CityLang.EN: "en"}
_WINDOW_SECONDS = 60.0


class PhotonGeocoder:
    """Searches cities by name through Photon."""

    def __init__(
        self,
        settings: GeocoderSettings,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a client.

        Args:
            settings: Endpoint, identity, cache and rate limit.
            transport: Replaces the network (tests).
            clock: Monotonic seconds (tests).
        """
        self._settings = settings
        self._transport = transport
        self._clock = clock
        self._cache: dict[tuple[str, CityLang], tuple[float, list[GeocoderHit]]] = {}
        self._calls: deque[float] = deque()

    def _cached(self, key: tuple[str, CityLang]) -> list[GeocoderHit] | None:
        entry = self._cache.get(key)
        if entry is None or entry[0] < self._clock():
            return None
        return entry[1]

    def _store(self, key: tuple[str, CityLang], hits: list[GeocoderHit]) -> None:
        if self._settings.cache_ttl_seconds == 0:
            return
        now = self._clock()
        if len(self._cache) >= self._settings.cache_max_entries:
            live = {k: v for k, v in self._cache.items() if v[0] >= now}
            self._cache = dict(list(live.items())[len(live) // 2 :])
        self._cache[key] = (now + self._settings.cache_ttl_seconds, hits)

    def _within_budget(self) -> bool:
        now = self._clock()
        while self._calls and self._calls[0] <= now - _WINDOW_SECONDS:
            self._calls.popleft()
        if len(self._calls) >= self._settings.max_requests_per_minute:
            return False
        self._calls.append(now)
        return True

    async def search(self, query: str, lang: CityLang) -> list[GeocoderHit] | None:
        """Find cities by a typed prefix.

        Args:
            query: What the user typed.
            lang: Language of the names.

        Returns:
            Hits in Photon's order, or None when the geocoder is unavailable
            (down, slow, malformed answer or over the request budget).
        """
        key = (slugify(query), lang)
        if (hits := self._cached(key)) is not None:
            return hits
        if not self._within_budget():
            logger.warning("Geocoder request budget used up")
            return None
        params = {
            "q": query,
            "layer": "city",
            "limit": str(self._settings.max_results),
        }
        if photon_lang := _PHOTON_LANG.get(lang):
            params["lang"] = photon_lang
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                headers={"User-Agent": self._settings.user_agent},
                transport=self._transport,
            ) as client:
                response = await client.get(self._settings.base_url, params=params)
                response.raise_for_status()
                hits = parse_photon(response.json())
        except httpx.HTTPError, ValueError, AttributeError:
            logger.warning("Geocoder request failed", exc_info=True)
            return None
        self._store(key, hits)
        return hits
