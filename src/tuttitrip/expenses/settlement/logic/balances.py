"""Balances and the smallest list of transfers, in integer cents (no I/O)."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from tuttitrip.expenses.logic.split import Share, allocate
from tuttitrip.expenses.schemas import SplitMethod

EXACT_LIMIT = 15
"""Up to this many people with a balance the transfers are minimal (subset DP)."""


@dataclass(frozen=True, slots=True)
class Spending:
    """One expense reduced to what settlement needs: who paid, how it is split."""

    payer: UUID
    cents: int
    method: SplitMethod
    shares: Sequence[Share]


@dataclass(frozen=True, slots=True)
class Transfer:
    """A payment of ``cents`` from a debtor to a creditor."""

    from_profile_id: UUID
    to_profile_id: UUID
    cents: int


def net_balances(spendings: Iterable[Spending]) -> dict[UUID, int]:
    """Credit each payer and debit each participant's share.

    Args:
        spendings: Expenses to settle.

    Returns:
        Cents per person (positive: owed money). The values sum to exactly 0.
    """
    balances: defaultdict[UUID, int] = defaultdict(int)
    for spending in spendings:
        balances[spending.payer] += spending.cents
        parts = allocate(spending.cents, spending.method, spending.shares)
        for profile_id, cents in parts.items():
            balances[profile_id] -= cents
    return dict(balances)


def apply_payment(balances: dict[UUID, int], payment: Transfer) -> None:
    """Move a paid transfer into the balances (the payer gains, the receiver loses).

    Args:
        balances: Cents per person, changed in place.
        payment: A transfer that was paid, in whole or in part.
    """
    balances[payment.from_profile_id] = (
        balances.get(payment.from_profile_id, 0) + payment.cents
    )
    balances[payment.to_profile_id] = (
        balances.get(payment.to_profile_id, 0) - payment.cents
    )


def settle(balances: dict[UUID, int]) -> list[Transfer]:
    """Smallest list of transfers that zeroes the balances, deterministic.

    The minimum is the number of people with a balance minus the largest number
    of disjoint groups that each sum to zero. With at most ``EXACT_LIMIT`` such
    people the groups come from a DP over subsets; above it the whole set is one
    group (at most n - 1 transfers). Inside a group the largest debtor pays the
    largest creditor (ties: lower profile id).

    Args:
        balances: Cents per person; must sum to 0.

    Returns:
        Transfers, ordered by debtor and creditor id.
    """
    people = sorted((p for p, c in balances.items() if c != 0), key=lambda p: p.int)
    groups = _zero_groups(people, balances) if len(people) <= EXACT_LIMIT else [people]
    transfers = [t for group in groups for t in _greedy(group, balances)]
    return sorted(transfers, key=lambda t: (t.from_profile_id.int, t.to_profile_id.int))


def _zero_groups(people: list[UUID], balances: dict[UUID, int]) -> list[list[UUID]]:
    n = len(people)
    values = [balances[p] for p in people]
    full = (1 << n) - 1
    sums = [0] * (full + 1)
    for mask in range(1, full + 1):
        low = (mask & -mask).bit_length() - 1
        sums[mask] = sums[mask & (mask - 1)] + values[low]
    best = [0] * (full + 1)
    last = [0] * (full + 1)  # index removed last, lowest on ties
    for mask in range(1, full + 1):
        pick, score = -1, -1
        for i in range(n):
            if mask >> i & 1 and best[mask ^ (1 << i)] > score:
                pick, score = i, best[mask ^ (1 << i)]
        best[mask] = score + (1 if sums[mask] == 0 else 0)
        last[mask] = pick
    order: list[int] = []
    mask = full
    while mask:
        order.append(last[mask])
        mask ^= 1 << last[mask]
    order.reverse()  # prefixes of this order are the DP chain
    groups: list[list[UUID]] = []
    current: list[UUID] = []
    running = 0
    for i in order:
        current.append(people[i])
        running += values[i]
        if running == 0:
            groups.append(current)
            current = []
    return groups


def _greedy(group: list[UUID], balances: dict[UUID, int]) -> list[Transfer]:
    left = {p: balances[p] for p in group}
    transfers: list[Transfer] = []
    while any(left.values()):
        debtor = min(left, key=lambda p: (left[p], p.int))
        creditor = min(left, key=lambda p: (-left[p], p.int))
        cents = min(-left[debtor], left[creditor])
        left[debtor] += cents
        left[creditor] -= cents
        transfers.append(Transfer(debtor, creditor, cents))
    return transfers
