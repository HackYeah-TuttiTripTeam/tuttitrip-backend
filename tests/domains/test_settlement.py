"""Settlement: cent-exact balances, the smallest deterministic transfer list."""

import random
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.expenses import db
from tuttitrip.expenses.logic.split import Share
from tuttitrip.expenses.models import Expense, ExpenseShare
from tuttitrip.expenses.schemas import SplitMethod
from tuttitrip.expenses.settlement.logic.balances import (
    EXACT_LIMIT,
    Spending,
    Transfer,
    net_balances,
    settle,
)
from tuttitrip.main import create_app
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service

A, B, C, D, E, F = (uuid.UUID(int=n) for n in (1, 2, 3, 4, 5, 6))
TRIP = uuid.uuid4()
USER = AuthenticatedUser(sub="auth0|member")


def _applied(
    balances: dict[uuid.UUID, int], transfers: list[Transfer]
) -> dict[uuid.UUID, int]:
    left = dict(balances)
    for t in transfers:
        assert t.cents > 0
        left[t.from_profile_id] = left.get(t.from_profile_id, 0) + t.cents
        left[t.to_profile_id] = left.get(t.to_profile_id, 0) - t.cents
    return left


def test_hundred_zloty_equal_on_three_people() -> None:
    spending = Spending(A, 10000, SplitMethod.EQUAL, [Share(A), Share(B), Share(C)])
    balances = net_balances([spending])
    # The extra cent goes to the lowest profile id (ties of the largest remainder).
    assert balances == {A: 10000 - 3334, B: -3333, C: -3333}
    assert sum(balances.values()) == 0
    assert settle(balances) == [Transfer(B, A, 3333), Transfer(C, A, 3333)]


def test_four_people_two_transfers() -> None:
    balances = {A: 1000, B: 500, C: -1000, D: -500}
    assert settle(balances) == [Transfer(C, A, 1000), Transfer(D, B, 500)]


def test_two_zero_sum_groups_beat_the_greedy_pairing() -> None:
    # Groups {+7, -4, -3} and {+6, -5, -1}: 4 transfers; largest-first needs 5.
    balances = {A: 7, B: 6, C: -5, D: -4, E: -3, F: -1}
    transfers = settle(balances)
    assert len(transfers) == 4
    assert set(_applied(balances, transfers).values()) == {0}


def test_excluded_person_keeps_a_zero_balance() -> None:
    spending = Spending(B, 3000, SplitMethod.EQUAL, [Share(B), Share(C)])
    balances = net_balances([spending])
    assert A not in balances
    assert balances == {B: 1500, C: -1500}


def test_percent_and_weights_to_the_cent() -> None:
    percent = Spending(
        A,
        10000,
        SplitMethod.PERCENT,
        [Share(A, Decimal(50)), Share(B, Decimal(30)), Share(C, Decimal(20))],
    )
    weights = Spending(
        B, 10000, SplitMethod.WEIGHTS, [Share(A, Decimal(1)), Share(C, Decimal(3))]
    )
    balances = net_balances([percent, weights])
    assert balances == {A: 5000 - 2500, B: -3000 + 10000 - 0, C: -2000 - 7500}
    assert sum(balances.values()) == 0


def test_order_of_expenses_does_not_change_the_transfers() -> None:
    rng = random.Random(7)  # ruff: ignore[suspicious-non-cryptographic-random-usage] (test data)
    people = [uuid.UUID(int=n) for n in range(1, 8)]
    spendings = [
        Spending(
            rng.choice(people),
            rng.randrange(100, 20000),
            SplitMethod.EQUAL,
            [Share(p) for p in rng.sample(people, rng.randrange(1, 7))],
        )
        for _ in range(12)
    ]
    expected = settle(net_balances(spendings))
    for _ in range(5):
        rng.shuffle(spendings)
        assert settle(net_balances(spendings)) == expected


def _max_zero_groups(values: list[int]) -> int:
    best = 0
    n = len(values)
    for mask in range(1, 1 << n):
        if sum(v for i, v in enumerate(values) if mask >> i & 1) == 0:
            rest = [v for i, v in enumerate(values) if not mask >> i & 1]
            best = max(best, 1 + _max_zero_groups(rest) if rest else 1)
    return best


@pytest.mark.parametrize("seed", range(12))
def test_count_is_people_minus_zero_sum_groups(seed: int) -> None:
    rng = random.Random(seed)  # ruff: ignore[suspicious-non-cryptographic-random-usage] (test data)
    n = rng.randrange(2, 8)
    values = [rng.randrange(-5, 6) for _ in range(n - 1)]
    values.append(-sum(values))
    people = [uuid.UUID(int=i + 1) for i in range(n)]
    balances = dict(zip(people, values, strict=True))
    nonzero = [v for v in values if v]
    transfers = settle(balances)
    assert set(_applied(balances, transfers).values()) <= {0}
    expected = len(nonzero) - _max_zero_groups(nonzero) if nonzero else 0
    assert len(transfers) == expected


def test_above_the_exact_limit_it_stays_below_n_minus_1() -> None:
    n = EXACT_LIMIT + 5
    rng = random.Random(3)  # ruff: ignore[suspicious-non-cryptographic-random-usage] (test data)
    values = [rng.randrange(-50, 51) or 1 for _ in range(n - 1)]
    values.append(-sum(values))
    balances = {uuid.UUID(int=i + 1): v for i, v in enumerate(values)}
    transfers = settle(balances)
    assert len(transfers) <= n - 1
    assert set(_applied(balances, transfers).values()) <= {0}


def _expense(payer: uuid.UUID, amount: str, people: tuple[uuid.UUID, ...]) -> Expense:
    return Expense(
        id=uuid.uuid4(),
        trip_id=TRIP,
        payer_profile_id=payer,
        amount=Decimal(amount),
        currency="PLN",
        trip_amount=Decimal(amount),
        description="",
        spent_on=date(2026, 11, 7),
        category=None,
        split_method=SplitMethod.EQUAL,
        created_by_sub=USER.sub,
        shares=[ExpenseShare(profile_id=p) for p in people],
    )


def _session() -> AsyncMock:
    return AsyncMock()


def _client(
    monkeypatch: pytest.MonkeyPatch, *, member: bool = True, grants: bool = True
) -> TestClient:
    def membership(_s: object, _t: uuid.UUID, sub: str, _r: TripRole) -> TripMembership:
        if not member:
            raise trip_service.TripNotFoundError(str(TRIP))
        return TripMembership(trip_id=TRIP, sub=sub, role=TripRole.MEMBER)

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=membership)
    )
    monkeypatch.setattr(
        trip_service,
        "get_trip",
        AsyncMock(return_value=TripRead.model_construct(currency="PLN")),
    )
    monkeypatch.setattr(
        profile_service,
        "list_profiles",
        AsyncMock(return_value=[ProfileRead.model_construct(id=i) for i in (A, B, C)]),
    )
    monkeypatch.setattr(
        db,
        "select_all",
        AsyncMock(return_value=[_expense(A, "100.00", (A, B, C))]),
    )
    app = create_app()
    if grants:
        authorize(app, USER)
    else:
        authorize(app, USER, (Grant(Feature.EXPENSES_CORE, Access.READ),))
    app.dependency_overrides[get_session] = _session
    return TestClient(app)


def test_endpoint_returns_balances_and_transfers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch).get(path("get_settlement", trip_id=TRIP))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currency"] == "PLN"
    assert Decimal(body["total_spent"]) == 100
    assert [Decimal(b["amount"]) for b in body["balances"]] == [
        Decimal("66.66"),
        Decimal("-33.33"),
        Decimal("-33.33"),
    ]
    assert [
        (t["from_profile_id"], Decimal(t["amount"])) for t in body["transfers"]
    ] == [
        (str(B), Decimal("33.33")),
        (str(C), Decimal("33.33")),
    ]


def test_outsider_gets_404_and_settlement_needs_its_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outsider = _client(monkeypatch, member=False)
    assert outsider.get(path("get_settlement", trip_id=TRIP)).status_code == 404
    denied = _client(monkeypatch, grants=False)
    assert denied.get(path("get_settlement", trip_id=TRIP)).status_code == 403
