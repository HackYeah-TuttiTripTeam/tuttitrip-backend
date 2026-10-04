"""Average exchange rates of the National Bank of Poland, cached per day.

``GET {base}/exchangerates/rates/{a|b}/{code}/{from}/{to}/?format=json``. Table A
covers the common currencies (daily), table B the rest (weekly). A day without
a quote (weekend, holiday, before publication) takes the last earlier one: the
query spans the previous 7 days (table A) or 14 days (table B) and we use the
newest row. See https://api.nbp.pl/en.html.
"""

import json
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from operator import itemgetter
from typing import Any

import httpx

from tuttitrip.expenses.logic.rates import PLN, Quote
from tuttitrip.shared.config.settings import get_settings

LOOKBACK_DAYS = {"a": 7, "b": 14}
RECENT_TTL = 3600.0
"""Seconds a quote is kept when the day may still get its own (today, or later)."""
MISSING_TTL = 600.0
"""Seconds "NBP has no rate for this currency" is remembered."""


class RateNotFoundError(Exception):
    """NBP publishes no average rate for this currency near this day."""


class RateUnavailableError(Exception):
    """NBP did not answer (network error, timeout or a server error)."""


class NbpClient:
    """Fetches and caches NBP quotes (a miss is not cached)."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Configure the HTTP client.

        Args:
            base_url: NBP API root, e.g. ``https://api.nbp.pl/api``.
            timeout_seconds: Total time allowed for one request.
            transport: Replaces the network (tests).
        """
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )
        # key -> (quote or None for "not found", monotonic expiry or None = forever)
        self._cache: dict[tuple[str, date], tuple[Quote | None, float | None]] = {}

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def quote(self, code: str, on: date) -> Quote:
        """Average rate of ``code`` for ``on`` or the last day before it.

        Past days are cached for good. For today (or later) a quote dated
        before the day may be replaced once NBP publishes, so it expires after
        an hour; "not found" is remembered for ten minutes.

        Args:
            code: ISO 4217 code.
            on: The day of the expense.

        Returns:
            The quote (PLN is 1 with no table).

        Raises:
            RateNotFoundError: Neither table has a quote in the look-back window.
            RateUnavailableError: NBP failed to answer.
        """
        if code == PLN:
            return Quote(PLN, Decimal(1), None, None)
        key = (code, on)
        hit = self._cache.get(key)
        if hit is not None and (hit[1] is None or hit[1] > time.monotonic()):
            if hit[0] is None:
                raise RateNotFoundError(code)
            return hit[0]
        try:
            quote = await self._fetch(code, on)
        except RateNotFoundError:
            self._cache[key] = (None, time.monotonic() + MISSING_TTL)
            raise
        provisional = on >= datetime.now(UTC).date() and (
            quote.effective_date is None or quote.effective_date < on
        )
        self._cache[key] = (
            quote,
            time.monotonic() + RECENT_TTL if provisional else None,
        )
        return quote

    async def _fetch(self, code: str, on: date) -> Quote:
        for table, days in LOOKBACK_DAYS.items():
            start = on - timedelta(days=days)
            url = f"/exchangerates/rates/{table}/{code.lower()}/{start}/{on}/"
            try:
                response = await self._client.get(url, params={"format": "json"})
            except httpx.HTTPError as exc:
                msg = f"NBP request failed: {type(exc).__name__}"
                raise RateUnavailableError(msg) from exc
            if response.status_code == httpx.codes.NOT_FOUND:
                continue
            if response.is_error:
                msg = f"NBP answered {response.status_code}"
                raise RateUnavailableError(msg)
            return _parse(code, response.text)
        raise RateNotFoundError(code)


def _parse(code: str, body: str) -> Quote:
    try:
        data: dict[str, Any] = json.loads(body, parse_float=Decimal)
        rate = max(data["rates"], key=itemgetter("effectiveDate"))
        return Quote(
            code=code,
            mid=Decimal(rate["mid"]),
            table_no=str(rate["no"]),
            effective_date=date.fromisoformat(rate["effectiveDate"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        msg = "NBP answered with an unexpected body"
        raise RateUnavailableError(msg) from exc


async def close_client() -> None:
    """Close the shared client if one was created (application shutdown)."""
    if get_client.cache_info().currsize:
        await get_client().aclose()
        get_client.cache_clear()


@lru_cache(maxsize=1)
def get_client() -> NbpClient:
    """The process-wide client, built from settings.

    Returns:
        The shared client (and its day cache).
    """
    nbp = get_settings().nbp
    return NbpClient(nbp.base_url, nbp.timeout_seconds)
