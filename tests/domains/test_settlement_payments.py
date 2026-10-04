"""Payments and closing: balances, who may mark, the 409 of a closed settlement."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.expenses import db as expense_db
from tuttitrip.expenses.logic.split import Share
from tuttitrip.expenses.models import Expense, ExpenseShare
from tuttitrip.expenses.schemas import SplitMethod
from tuttitrip.expenses.settlement import db
from tuttitrip.expenses.settlement.logic.balances import (
    Spending,
    Transfer,
    apply_payment,
    net_balances,
    settle,
)
from tuttitrip.expenses.settlement.models import SettlementPayment, TripSettlement
from tuttitrip.main import create_app
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service

TRIP = uuid.uuid4()
A, B, C = (uuid.UUID(int=n) for n in (1, 2, 3))
USER = AuthenticatedUser(sub="auth0|c")  # owns profile C
HOST_SUB = "auth0|host"


def test_paid_transfer_zeroes_both_balances_and_disappears() -> None:
    spending = Spending(A, 9000, SplitMethod.EQUAL, [Share(A), Share(B), Share(C)])
    balances = net_balances([spending])
    assert settle(balances) == [Transfer(B, A, 3000), Transfer(C, A, 3000)]
    apply_payment(balances, Transfer(C, A, 3000))
    assert balances == {A: 3000, B: -3000, C: 0}
    assert settle(balances) == [Transfer(B, A, 3000)]


def test_partial_payment_shrinks_the_transfer_and_overpaying_flips_it() -> None:
    balances = {A: 3000, C: -3000}
    apply_payment(balances, Transfer(C, A, 1000))
    assert settle(balances) == [Transfer(C, A, 2000)]
    apply_payment(balances, Transfer(C, A, 3000))  # 10.00 too much
    assert settle(balances) == [Transfer(A, C, 1000)]


def _expense() -> Expense:
    return Expense(
        id=uuid.uuid4(),
        trip_id=TRIP,
        payer_profile_id=A,
        amount=Decimal("90.00"),
        currency="PLN",
        trip_amount=Decimal("90.00"),
        description="",
        spent_on=date(2026, 11, 7),
        category=None,
        split_method=SplitMethod.EQUAL,
        created_by_sub="auth0|a",
        shares=[ExpenseShare(profile_id=p) for p in (A, B, C)],
    )


def _payment(sub: str = "auth0|x") -> SettlementPayment:
    return SettlementPayment(
        id=uuid.uuid4(),
        trip_id=TRIP,
        from_profile_id=C,
        to_profile_id=A,
        amount=Decimal("30.00"),
        paid_on=date(2026, 11, 8),
        marked_by_sub=sub,
        created_at=datetime(2026, 11, 8, tzinfo=UTC),
    )


def _session() -> AsyncMock:
    return AsyncMock()


def _client(  # ruff: ignore[too-many-arguments] test helper
    monkeypatch: pytest.MonkeyPatch,
    role: TripRole = TripRole.MEMBER,
    *,
    sub: str = USER.sub,
    paid: tuple[SettlementPayment, ...] = (),
    closed: bool = False,
    drafts: int = 0,
) -> TestClient:
    def membership(
        _s: object, _t: uuid.UUID, who: str, min_role: TripRole
    ) -> TripMembership:
        if not role.satisfies(min_role):
            raise trip_service.TripRoleError(min_role)
        return TripMembership(trip_id=TRIP, sub=who, role=role)

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=membership)
    )
    monkeypatch.setattr(
        trip_service,
        "get_trip",
        AsyncMock(return_value=TripRead.model_construct(currency="PLN")),
    )
    people = [
        ProfileRead.model_construct(id=A, user_sub="auth0|a"),
        ProfileRead.model_construct(id=B, user_sub="auth0|b"),
        ProfileRead.model_construct(id=C, user_sub=USER.sub),
    ]
    monkeypatch.setattr(
        profile_service, "list_profiles", AsyncMock(return_value=people)
    )
    monkeypatch.setattr(expense_db, "select_all", AsyncMock(return_value=[_expense()]))
    monkeypatch.setattr(db, "select_all_payments", AsyncMock(return_value=list(paid)))
    closure = TripSettlement(
        trip_id=TRIP,
        closed_by_sub=HOST_SUB,
        closed_at=datetime(2026, 11, 9, tzinfo=UTC),
    )
    monkeypatch.setattr(
        db, "select_closure", AsyncMock(return_value=closure if closed else None)
    )

    def stored(_session: object, payment: SettlementPayment) -> None:
        payment.id = uuid.uuid4()
        payment.created_at = datetime(2026, 11, 8, tzinfo=UTC)

    monkeypatch.setattr(expense_db, "count_drafts", AsyncMock(return_value=drafts))
    monkeypatch.setattr(db, "insert_payment", AsyncMock(side_effect=stored))
    monkeypatch.setattr(db, "insert_closure", AsyncMock())
    monkeypatch.setattr(db, "delete_closure", AsyncMock())
    monkeypatch.setattr(
        db, "select_payment", AsyncMock(return_value=paid[0] if paid else None)
    )
    monkeypatch.setattr(db, "delete_payment", AsyncMock())
    app = create_app()
    authorize(app, AuthenticatedUser(sub=sub))
    app.dependency_overrides[get_session] = _session
    return TestClient(app)


BODY = {"from_profile_id": str(C), "to_profile_id": str(A), "amount": "30.00"}


def test_settlement_counts_paid_transfers(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, paid=(_payment(),))
    body = client.get(path("get_settlement", trip_id=TRIP)).json()
    assert [Decimal(b["amount"]) for b in body["balances"]] == [30, -30, 0]
    assert [(t["from_profile_id"], t["to_profile_id"]) for t in body["transfers"]] == [
        (str(B), str(A))
    ]
    assert body["closed_at"] is None


def test_a_party_marks_a_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)  # the caller is profile C, the payer
    response = client.post(path("mark_paid", trip_id=TRIP), json=BODY)
    assert response.status_code == 201, response.text
    assert response.json()["marked_by_sub"] == USER.sub


def test_host_marks_for_others_but_a_bystander_gets_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = {**BODY, "from_profile_id": str(B)}  # B to A: the caller (C) is a stranger
    denied = _client(monkeypatch).post(path("mark_paid", trip_id=TRIP), json=other)
    assert denied.status_code == 403
    host = _client(monkeypatch, TripRole.HOST, sub=HOST_SUB)
    assert host.post(path("mark_paid", trip_id=TRIP), json=other).status_code == 201


def test_payment_rules_answer_422_with_a_code(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, TripRole.HOST, sub=HOST_SUB)
    same = client.post(
        path("mark_paid", trip_id=TRIP), json={**BODY, "to_profile_id": str(C)}
    )
    assert same.json()["detail"][0]["type"] == "payment.same_person"
    stranger = client.post(
        path("mark_paid", trip_id=TRIP),
        json={**BODY, "to_profile_id": str(uuid.uuid4())},
    )
    assert stranger.json()["detail"][0]["type"] == "payment.person_not_on_trip"
    zero = client.post(path("mark_paid", trip_id=TRIP), json={**BODY, "amount": "0"})
    assert zero.status_code == 422


def test_delete_a_payment_by_a_party_or_host_not_a_bystander(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment = _payment()
    url = path("remove_payment", trip_id=TRIP, payment_id=payment.id)
    assert _client(monkeypatch, paid=(payment,)).delete(url).status_code == 204
    stranger = _client(monkeypatch, sub="auth0|b", paid=(payment,))
    assert stranger.delete(url).status_code == 403
    host = _client(monkeypatch, TripRole.HOST, sub=HOST_SUB, paid=(payment,))
    assert host.delete(url).status_code == 204
    missing = path("remove_payment", trip_id=TRIP, payment_id=uuid.uuid4())
    assert _client(monkeypatch).delete(missing).status_code == 404


def test_only_the_host_closes_and_reopens(monkeypatch: pytest.MonkeyPatch) -> None:
    member = _client(monkeypatch)
    assert member.post(path("close_settlement", trip_id=TRIP)).status_code == 403
    assert member.post(path("reopen_settlement", trip_id=TRIP)).status_code == 403
    host = _client(monkeypatch, TripRole.HOST, sub=HOST_SUB)
    assert host.post(path("close_settlement", trip_id=TRIP)).status_code == 200
    assert host.post(path("reopen_settlement", trip_id=TRIP)).status_code == 200


def test_closed_settlement_refuses_expenses_and_payments_with_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, closed=True)
    body = client.get(path("get_settlement", trip_id=TRIP)).json()
    assert body["closed_at"] is not None
    expense = {
        "payer_profile_id": str(A),
        "amount": "10.00",
        "spent_on": "2026-11-07",
        "participants": [{"profile_id": str(A)}],
    }
    created = client.post(path("create_expense", trip_id=TRIP), json=expense)
    assert created.status_code == 409
    assert "host" in created.json()["detail"]
    assert client.post(path("mark_paid", trip_id=TRIP), json=BODY).status_code == 409


def test_close_is_refused_while_drafts_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _client(monkeypatch, TripRole.HOST, sub=HOST_SUB, drafts=2)
    response = host.post(path("close_settlement", trip_id=TRIP))
    assert response.status_code == 409
    assert "2 draft" in response.json()["detail"]
