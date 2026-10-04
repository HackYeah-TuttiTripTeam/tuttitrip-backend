"""Average exchange rates of the National Bank of Poland, cached per day.

``GET {base}/exchangerates/rates/{a|b}/{code}/{from}/{to}/?format=json``. Table A
covers the common currencies (daily), table B the rest (weekly). A day without
a quote (weekend, holiday, before publication) takes the last earlier one: the
query spans the previous 7 days (table A) or 14 days (table B) and we use the
newest row. See https://api.nbp.pl/en.html.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from operator import itemgetter
from typing import Any

import httpx

from tuttitrip.expenses.logic.rates import PLN, Quote
from tuttitrip.shared.config.settings import get_settings

LOOKBACK_DAYS = {"a": 7, "b": 14}


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
        self._cache: dict[tuple[str, date], Quote] = {}

    async def quote(self, code: str, on: date) -> Quote:
        """Average rate of ``code`` for ``on`` or the last day before it.

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
        if key not in self._cache:
            self._cache[key] = await self._fetch(code, on)
        return self._cache[key]

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


@lru_cache(maxsize=1)
def get_client() -> NbpClient:
    """The process-wide client, built from settings.

    Returns:
        The shared client (and its day cache).
    """
    nbp = get_settings().nbp
    return NbpClient(nbp.base_url, nbp.timeout_seconds)
