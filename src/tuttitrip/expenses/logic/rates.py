"""Exchange-rate math in integer cents (no I/O)."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

RATE_PLACES = Decimal("0.00000001")
NOT_TWO_DECIMALS = frozenset(
    [
        "BHD",
        "CLP",
        "DJF",
        "GNF",
        "IQD",
        "ISK",
        "JOD",
        "JPY",
        "KMF",
        "KRW",
        "KWD",
        "LYD",
        "OMR",
        "PYG",
        "RWF",
        "TND",
        "UGX",
        "UYI",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    ]
)
"""ISO 4217 currencies without 2 minor-unit digits; amounts are 2-decimal here."""


@dataclass(frozen=True, slots=True)
class Quote:
    """An NBP average rate: how many PLN one unit of ``code`` costs."""

    code: str
    mid: Decimal
    table_no: str | None
    effective_date: date | None


PLN = "PLN"


def has_two_decimals(code: str) -> bool:
    """Whether a currency uses 2 decimal places (the only kind we support).

    Args:
        code: ISO 4217 code.

    Returns:
        False for currencies like JPY, KWD or CLP.
    """
    return code not in NOT_TWO_DECIMALS


def cross_rate(foreign_pln: Decimal, trip_pln: Decimal) -> Decimal:
    """Rate from a foreign currency to the trip currency, through PLN.

    Args:
        foreign_pln: PLN per unit of the expense currency.
        trip_pln: PLN per unit of the trip currency (1 for PLN).

    Returns:
        Trip-currency units per unit of the expense currency, 8 places.
    """
    return (foreign_pln / trip_pln).quantize(RATE_PLACES, ROUND_HALF_UP)


def convert_cents(cents: int, rate: Decimal) -> int:
    """Convert cents at a rate, half up to the cent.

    Args:
        cents: Amount in the expense currency.
        rate: Trip-currency units per unit of the expense currency.

    Returns:
        Amount in the trip currency, in cents.
    """
    return int((Decimal(cents) * rate).quantize(Decimal(1), ROUND_HALF_UP))
