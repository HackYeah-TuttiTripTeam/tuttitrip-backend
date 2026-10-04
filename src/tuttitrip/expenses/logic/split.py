"""Rules for one expense and its split, in integer cents (no I/O)."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

from tuttitrip.expenses.schemas import ExpenseErrorCode, SplitMethod

CENTS = 100
PERCENT_TOTAL = Decimal(100)


@dataclass(frozen=True, slots=True)
class Share:
    """A participant and their entered value (``None`` for an equal split)."""

    profile_id: UUID
    value: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Violation:
    """A broken rule: a stable code, the request field and a message for people."""

    code: ExpenseErrorCode
    field: str
    message: str


def to_cents(amount: Decimal) -> int:
    """Convert a two-decimal amount to integer cents.

    Args:
        amount: Amount with at most 2 decimal places.

    Returns:
        The amount in cents.
    """
    return int(amount * CENTS)


def check_expense(  # ruff: ignore[too-many-arguments] one rule set, merged state
    *,
    amount: Decimal,
    currency: str | None,
    trip_currency: str | None,
    payer: UUID,
    method: SplitMethod,
    shares: Sequence[Share],
    trip_profiles: Collection[UUID],
) -> list[Violation]:
    """Check an expense (the whole merged state) against the trip.

    Args:
        amount: The cost.
        currency: Currency of the expense, if given.
        trip_currency: Currency of the trip, if it has one.
        payer: Profile that paid.
        method: How the cost is divided.
        shares: Participants with their entered values.
        trip_profiles: Ids of the people on the trip.

    Returns:
        Every broken rule (empty when the expense is valid).
    """
    found: list[Violation] = []
    if amount <= 0:
        found.append(
            Violation(
                ExpenseErrorCode.AMOUNT_NOT_POSITIVE,
                "amount",
                "The amount must be greater than zero",
            )
        )
    if currency is None and trip_currency is None:
        found.append(
            Violation(
                ExpenseErrorCode.CURRENCY_REQUIRED,
                "currency",
                "The trip has no currency, so the expense needs one",
            )
        )
    elif currency is not None and trip_currency not in {None, currency}:
        found.append(
            Violation(
                ExpenseErrorCode.CURRENCY_MISMATCH,
                "currency",
                f"The expense must be in the trip's currency ({trip_currency})",
            )
        )
    if payer not in trip_profiles:
        found.append(
            Violation(
                ExpenseErrorCode.PAYER_NOT_ON_TRIP,
                "payer_profile_id",
                "The payer is not on this trip",
            )
        )
    return [*found, *_check_shares(method, shares, trip_profiles)]


def _check_shares(
    method: SplitMethod, shares: Sequence[Share], trip_profiles: Collection[UUID]
) -> list[Violation]:
    found: list[Violation] = []
    field = "participants"
    if not shares:
        return [
            Violation(
                ExpenseErrorCode.PARTICIPANTS_REQUIRED,
                field,
                "At least one participant is required",
            )
        ]
    ids = [share.profile_id for share in shares]
    if len(set(ids)) != len(ids):
        found.append(
            Violation(
                ExpenseErrorCode.PARTICIPANT_DUPLICATED,
                field,
                "A participant can be listed only once",
            )
        )
    if not set(ids) <= set(trip_profiles):
        found.append(
            Violation(
                ExpenseErrorCode.PARTICIPANT_NOT_ON_TRIP,
                field,
                "A participant is not on this trip",
            )
        )
    values = [share.value for share in shares]
    if method is SplitMethod.EQUAL:
        if any(value is not None for value in values):
            found.append(
                Violation(
                    ExpenseErrorCode.SHARE_VALUE_NOT_ALLOWED,
                    field,
                    "An equal split takes no values",
                )
            )
    elif any(value is None for value in values):
        found.append(
            Violation(
                ExpenseErrorCode.SHARE_VALUE_REQUIRED,
                field,
                f"Every participant needs a value for the {method} split",
            )
        )
    else:
        found.extend(_check_values(method, [v for v in values if v is not None]))
    return found


def _check_values(method: SplitMethod, values: Sequence[Decimal]) -> list[Violation]:
    field = "participants"
    if any(value <= 0 for value in values):
        return [
            Violation(
                ExpenseErrorCode.WEIGHT_NOT_POSITIVE,
                field,
                "Every value must be greater than zero",
            )
        ]
    if method is SplitMethod.PERCENT and sum(values) != PERCENT_TOTAL:
        return [
            Violation(
                ExpenseErrorCode.PERCENT_SUM,
                field,
                f"The percentages must add up to 100 (they add up to {sum(values)})",
            )
        ]
    return []


def allocate(
    total_cents: int, method: SplitMethod, shares: Sequence[Share]
) -> dict[UUID, int]:
    """Divide a cost among participants, to the cent, with no cent lost.

    Each part is rounded down, then the leftover cents go one by one to the
    largest remainders (ties: lower profile id first), so the result is
    deterministic and always adds up to ``total_cents``.

    Args:
        total_cents: The cost in cents.
        method: How the cost is divided.
        shares: Valid participants (see ``check_expense``).

    Returns:
        Cents owed by each participant.
    """
    weights = {
        share.profile_id: Fraction(share.value)
        if method is not SplitMethod.EQUAL and share.value is not None
        else Fraction(1)
        for share in shares
    }
    denominator = sum(weights.values())
    exact = {pid: total_cents * w / denominator for pid, w in weights.items()}
    parts = {pid: int(value) for pid, value in exact.items()}  # floor: all >= 0
    by_remainder = sorted(exact, key=lambda pid: (-(exact[pid] - parts[pid]), pid.int))
    for pid in by_remainder[: total_cents - sum(parts.values())]:
        parts[pid] += 1
    return parts
