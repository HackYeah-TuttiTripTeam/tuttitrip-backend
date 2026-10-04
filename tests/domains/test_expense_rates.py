"""Foreign-currency expenses: NBP client (offline), rate math and pricing."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from functools import wraps
from pathlib import Path

import httpx
import pytest

from tuttitrip.expenses.logic.rates import PLN, Quote, convert_cents, cross_rate
from tuttitrip.expenses.models import Expense
from tuttitrip.expenses.schemas import ExpenseErrorCode
from tuttitrip.expenses.services import expense_service, nbp_client
from tuttitrip.expenses.services.nbp_client import (
    NbpClient,
    RateNotFoundError,
    RateUnavailableError,
)

# Trimmed recording of the real answer for EUR on 2026-09-27 (a Sunday): the
# range query returns the last quote, table 187/A/NBP/2026 of Friday 25 September.
EUR_A = (Path(__file__).parent.parent / "fixtures/nbp/eur_table_a.json").read_text()
SUNDAY = date(2026, 9, 27)


def sync[**P](test: Callable[P, Awaitable[None]]) -> Callable[P, None]:
    """Run an async test body (the suite has no asyncio plugin)."""

    @wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))  # type: ignore[arg-type]

    return run


def _client(handler: httpx.MockTransport) -> NbpClient:
    return NbpClient("https://nbp.test/api", 1.0, transport=handler)


@sync
async def test_sunday_takes_the_last_quote_of_table_a() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=EUR_A)

    quote = await _client(httpx.MockTransport(handle)).quote("EUR", SUNDAY)
    assert seen == ["/api/exchangerates/rates/a/eur/2026-09-20/2026-09-27/"]
    assert (quote.table_no, quote.effective_date, quote.mid) == (
        "187/A/NBP/2026",
        date(2026, 9, 25),
        Decimal("4.3750"),
    )
    assert quote.mid.as_tuple().exponent == -4  # a Decimal, not a float


@sync
async def test_quotes_are_cached_per_day() -> None:
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=EUR_A)

    client = _client(httpx.MockTransport(handle))
    await client.quote("EUR", SUNDAY)
    await client.quote("EUR", SUNDAY)
    assert calls == 1
    await client.quote("EUR", date(2026, 9, 26))
    assert calls == 2


@sync
async def test_currency_outside_table_a_falls_back_to_table_b() -> None:
    body = json.dumps(
        {"rates": [{"no": "039/B/NBP/2026", "effectiveDate": "2026-09-23", "mid": 0.5}]}
    )
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path.split("/")[4])
        if "/rates/a/" in request.url.path:
            return httpx.Response(404, text="404 NotFound")
        return httpx.Response(200, text=body)

    quote = await _client(httpx.MockTransport(handle)).quote("XYZ", SUNDAY)
    assert seen == ["a", "b"]
    assert quote.table_no == "039/B/NBP/2026"


@sync
async def test_unknown_currency_is_not_found_and_pln_needs_no_call() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(RateNotFoundError):
        await client.quote("XYZ", SUNDAY)
    assert (await client.quote(PLN, SUNDAY)).mid == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, text='{"rates": []}'),
    ],
)
@sync
async def test_nbp_failures_are_unavailable(response: httpx.Response) -> None:
    client = _client(httpx.MockTransport(lambda _request: response))
    with pytest.raises(RateUnavailableError):
        await client.quote("EUR", SUNDAY)


@sync
async def test_network_error_is_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        message = "slow"
        raise httpx.ConnectTimeout(message, request=request)

    with pytest.raises(RateUnavailableError):
        await _client(httpx.MockTransport(handle)).quote("EUR", SUNDAY)


def test_conversion_rounds_half_up_to_the_cent() -> None:
    assert convert_cents(2000, Decimal("4.3750")) == 8750  # 20 EUR = 87.50 PLN
    assert convert_cents(1, Decimal("0.5")) == 1
    assert convert_cents(333, Decimal("1.5")) == 500  # 499.5 rounds up


def test_cross_rate_goes_through_pln() -> None:
    # 1 EUR = 4.3750 PLN, 1 USD = 4.0000 PLN
    assert cross_rate(Decimal("4.3750"), Decimal("4.0000")) == Decimal("1.09375000")
    assert cross_rate(Decimal("4.3750"), Decimal(1)) == Decimal("4.37500000")


class _Fake:
    """Stands in for the shared client; ``down`` makes NBP fail."""

    def __init__(self, *, down: bool = False) -> None:
        self.down = down

    async def quote(self, code: str, _on: date) -> Quote:
        if self.down:
            raise RateUnavailableError
        mids = {"EUR": "4.3750", "USD": "4.0000", "PLN": "1"}
        table = None if code == PLN else f"187/A/NBP/2026-{code}"
        return Quote(code, Decimal(mids[code]), table, date(2026, 9, 25))


async def _price(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trip: str | None = "PLN",
    manual: str | None = None,
    down: bool = False,
) -> expense_service.Pricing:
    monkeypatch.setattr(nbp_client, "get_client", lambda: _Fake(down=down))
    return await expense_service.price(
        amount=Decimal("20.00"),
        currency="EUR",
        trip_currency=trip,
        spent_on=SUNDAY,
        manual_rate=Decimal(manual) if manual else None,
    )


@sync
async def test_price_in_pln_keeps_table_and_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing = await _price(monkeypatch)
    assert pricing.trip_amount == Decimal("87.50")
    assert (pricing.rate, pricing.source) == (Decimal("4.375"), "nbp")
    assert pricing.rate_date == date(2026, 9, 25)
    assert pricing.table == "187/A/NBP/2026-EUR"


@sync
async def test_price_in_another_trip_currency_uses_both_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing = await _price(monkeypatch, trip="USD")
    assert pricing.trip_amount == Decimal("21.88")  # 20 * 1.09375 = 21.875
    assert pricing.table == "187/A/NBP/2026-EUR, 187/A/NBP/2026-USD"


@sync
async def test_same_currency_or_no_trip_currency_needs_no_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for trip in ("EUR", None):
        pricing = await _price(monkeypatch, trip=trip, down=True)
        assert (pricing.trip_amount, pricing.rate) == (Decimal("20.00"), None)


@sync
async def test_nbp_down_asks_for_a_manual_rate_and_manual_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(expense_service.ExpenseInvalidError) as caught:
        await _price(monkeypatch, down=True)
    assert [v.code for v in caught.value.violations] == [
        ExpenseErrorCode.RATE_UNAVAILABLE
    ]
    assert caught.value.violations[0].field == "manual_rate"
    pricing = await _price(monkeypatch, manual="4.5", down=True)
    assert (pricing.trip_amount, pricing.source, pricing.table) == (
        Decimal("90.00"),
        "manual",
        None,
    )


@sync
async def test_stored_rate_survives_edits_but_not_a_new_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nbp_client, "get_client", lambda: _Fake(down=True))
    stored = Expense(
        amount=Decimal("20.00"),
        currency="EUR",
        spent_on=SUNDAY,
        rate=Decimal("4.2"),
        rate_source="nbp",
        rate_table="100/A/NBP/2026",
        rate_date=date(2026, 9, 25),
    )
    kept = await expense_service.price(
        amount=Decimal("30.00"),
        currency="EUR",
        trip_currency="PLN",
        spent_on=SUNDAY,
        manual_rate=None,
        stored=stored,
    )
    assert (kept.trip_amount, kept.rate, kept.table) == (
        Decimal("126.00"),
        Decimal("4.2"),
        "100/A/NBP/2026",
    )
    with pytest.raises(expense_service.ExpenseInvalidError):  # NBP is "down"
        await expense_service.price(
            amount=Decimal("30.00"),
            currency="EUR",
            trip_currency="PLN",
            spent_on=date(2026, 9, 28),
            manual_rate=None,
            stored=stored,
        )
