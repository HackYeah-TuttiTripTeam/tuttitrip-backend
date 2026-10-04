"""Expenses: pure split rules, the service's access rules and the endpoints."""

import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.expenses import db
from tuttitrip.expenses.logic.split import (
    Share,
    Violation,
    allocate,
    check_expense,
    to_cents,
)
from tuttitrip.expenses.models import Expense, ExpenseShare
from tuttitrip.expenses.schemas import (
    ExpenseCreate,
    ExpenseErrorCode,
    ExpenseUpdate,
    SplitMethod,
)
from tuttitrip.expenses.services import expense_service
from tuttitrip.main import create_app
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service

TRIP = uuid.uuid4()
KASIA, ANIA, TOMEK = (uuid.UUID(int=n) for n in (1, 2, 3))
PEOPLE = {KASIA, ANIA, TOMEK}
AUTHOR = AuthenticatedUser(sub="auth0|author")
OTHER = AuthenticatedUser(sub="auth0|other")


def _violations(  # ruff: ignore[too-many-arguments] test helper
    method: SplitMethod = SplitMethod.EQUAL,
    shares: tuple[Share, ...] = (Share(KASIA), Share(TOMEK)),
    *,
    amount: str = "142.00",
    currency: str | None = "PLN",
    trip_currency: str | None = "PLN",
    payer: uuid.UUID = KASIA,
) -> list[ExpenseErrorCode]:
    found: list[Violation] = check_expense(
        amount=Decimal(amount),
        currency=currency,
        trip_currency=trip_currency,
        payer=payer,
        method=method,
        shares=shares,
        trip_profiles=PEOPLE,
    )
    return [v.code for v in found]


def test_valid_equal_split_without_ania() -> None:
    assert _violations() == []


def test_percentages_must_add_up_to_100() -> None:
    shares = tuple(
        Share(p, Decimal(v)) for p, v in ((KASIA, 50), (ANIA, 30), (TOMEK, 10))
    )
    assert _violations(SplitMethod.PERCENT, shares) == [ExpenseErrorCode.PERCENT_SUM]
    ok = tuple(Share(p, Decimal(v)) for p, v in ((KASIA, 50), (ANIA, 30), (TOMEK, 20)))
    assert _violations(SplitMethod.PERCENT, ok) == []


def test_weights_must_be_positive_and_present() -> None:
    zero = (Share(KASIA, Decimal(0)), Share(ANIA, Decimal(1)))
    assert _violations(SplitMethod.WEIGHTS, zero) == [
        ExpenseErrorCode.SHARE_VALUE_NOT_POSITIVE
    ]
    missing = (Share(KASIA, Decimal(1)), Share(ANIA))
    assert _violations(SplitMethod.WEIGHTS, missing) == [
        ExpenseErrorCode.SHARE_VALUE_REQUIRED
    ]


def test_equal_split_takes_no_values() -> None:
    shares = (Share(KASIA, Decimal(1)),)
    assert _violations(shares=shares) == [ExpenseErrorCode.SHARE_VALUE_NOT_ALLOWED]


def test_people_must_be_on_the_trip_and_listed_once() -> None:
    stranger = uuid.UUID(int=99)
    assert _violations(payer=stranger) == [ExpenseErrorCode.PAYER_NOT_ON_TRIP]
    assert _violations(shares=(Share(stranger),)) == [
        ExpenseErrorCode.PARTICIPANT_NOT_ON_TRIP
    ]
    assert _violations(shares=(Share(KASIA), Share(KASIA))) == [
        ExpenseErrorCode.PARTICIPANT_DUPLICATED
    ]
    assert _violations(shares=()) == [ExpenseErrorCode.PARTICIPANTS_REQUIRED]


@pytest.mark.parametrize(
    ("amount", "currency", "trip_currency", "codes"),
    [
        ("0", "PLN", "PLN", [ExpenseErrorCode.AMOUNT_NOT_POSITIVE]),
        ("10", "EUR", "PLN", []),
        ("10", "JPY", "PLN", [ExpenseErrorCode.CURRENCY_UNSUPPORTED]),
        ("10", None, None, [ExpenseErrorCode.CURRENCY_REQUIRED]),
        ("10", "EUR", None, []),
        ("10", None, "PLN", []),
    ],
)
def test_amount_and_currency(
    amount: str, currency: str | None, trip_currency: str | None, codes: list[str]
) -> None:
    got = _violations(amount=amount, currency=currency, trip_currency=trip_currency)
    assert got == codes


def test_to_cents_is_exact() -> None:
    assert to_cents(Decimal("142.00")) == 14200
    assert to_cents(Decimal("0.07")) == 7


@pytest.mark.parametrize(
    ("total", "method", "values", "expected"),
    [
        (10000, SplitMethod.EQUAL, (None, None, None), (3334, 3333, 3333)),
        (10000, SplitMethod.PERCENT, ("50", "30", "20"), (5000, 3000, 2000)),
        (10000, SplitMethod.PERCENT, ("33.33", "33.33", "33.34"), (3333, 3333, 3334)),
        (10000, SplitMethod.WEIGHTS, ("1", "2", "1"), (2500, 5000, 2500)),
        (1, SplitMethod.EQUAL, (None, None, None), (1, 0, 0)),
    ],
)
def test_allocation_never_loses_a_cent(
    total: int,
    method: SplitMethod,
    values: tuple[str | None, ...],
    expected: tuple[int, ...],
) -> None:
    ids = (KASIA, ANIA, TOMEK)
    shares = [
        Share(i, None if v is None else Decimal(v))
        for i, v in zip(ids, values, strict=True)
    ]
    parts = allocate(total, method, shares)
    assert tuple(parts[i] for i in ids) == expected
    assert sum(parts.values()) == total


def test_patch_rejects_null_for_required_fields_but_not_category() -> None:
    assert ExpenseUpdate.model_validate({"category": None}).category is None
    with pytest.raises(ValidationError) as caught:
        ExpenseUpdate.model_validate({"amount": None})
    (error,) = caught.value.errors()
    assert error["type"] == "expense.null_not_allowed"
    assert error["loc"] == ("amount",)


def test_create_rejects_unknown_fields_and_empty_participants() -> None:
    body: dict[str, Any] = {
        "payer_profile_id": str(KASIA),
        "amount": "10.00",
        "spent_on": "2026-11-07",
        "participants": [],
    }
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(body)
    body["participants"] = [{"profile_id": str(KASIA)}]
    assert ExpenseCreate.model_validate(body).split_method is SplitMethod.EQUAL
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate({**body, "amount": "10.001"})


def _membership(role: TripRole, sub: str = OTHER.sub) -> TripMembership:
    return TripMembership(trip_id=TRIP, sub=sub, role=role)


def _expense() -> Expense:
    return Expense(
        id=uuid.uuid4(),
        trip_id=TRIP,
        payer_profile_id=KASIA,
        amount=Decimal("142.00"),
        currency="PLN",
        trip_amount=Decimal("142.00"),
        description="Kolacja",
        spent_on=date(2026, 11, 7),
        category=None,
        split_method=SplitMethod.EQUAL,
        created_by_sub=AUTHOR.sub,
        created_at=datetime(2026, 11, 7, 20, 0, tzinfo=UTC),
        shares=[ExpenseShare(profile_id=KASIA), ExpenseShare(profile_id=TOMEK)],
    )


def _trip(currency: str | None = "PLN") -> TripRead:
    return TripRead.model_construct(currency=currency)


def _profiles() -> list[ProfileRead]:
    return [ProfileRead.model_construct(id=i) for i in PEOPLE]


def _patch_service(
    monkeypatch: pytest.MonkeyPatch, expense: Expense | None = None
) -> AsyncMock:
    monkeypatch.setattr(trip_service, "get_trip", AsyncMock(return_value=_trip()))
    monkeypatch.setattr(
        profile_service, "list_profiles", AsyncMock(return_value=_profiles())
    )
    monkeypatch.setattr(db, "select_expense", AsyncMock(return_value=expense))
    monkeypatch.setattr(db, "insert_expense", AsyncMock())
    monkeypatch.setattr(db, "replace_shares", AsyncMock())
    deleted = AsyncMock()
    monkeypatch.setattr(db, "delete_expense", deleted)
    return deleted


def _session() -> AsyncMock:
    return AsyncMock()


@pytest.mark.parametrize(
    ("role", "sub", "allowed"),
    [
        (TripRole.MEMBER, AUTHOR.sub, True),
        (TripRole.MEMBER, OTHER.sub, False),
        (TripRole.CO_HOST, OTHER.sub, True),
        (TripRole.HOST, OTHER.sub, True),
    ],
)
def test_only_author_or_hosts_delete(
    monkeypatch: pytest.MonkeyPatch, role: TripRole, sub: str, *, allowed: bool
) -> None:
    deleted = _patch_service(monkeypatch, _expense())
    run = expense_service.delete_expense(
        _session(), _membership(role, sub), uuid.uuid4()
    )
    if allowed:
        asyncio.run(run)
        deleted.assert_awaited_once()
    else:
        with pytest.raises(expense_service.ExpenseForbiddenError):
            asyncio.run(run)
        deleted.assert_not_awaited()


def test_update_checks_the_merged_expense(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_service(monkeypatch, _expense())
    to_percent = ExpenseUpdate(split_method=SplitMethod.PERCENT)
    with pytest.raises(expense_service.ExpenseInvalidError) as caught:
        asyncio.run(
            expense_service.update_expense(
                _session(), _membership(TripRole.HOST), uuid.uuid4(), to_percent
            )
        )
    assert [v.code for v in caught.value.violations] == [
        ExpenseErrorCode.SHARE_VALUE_REQUIRED
    ]


def test_update_of_a_missing_expense_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_service(monkeypatch, None)
    with pytest.raises(expense_service.ExpenseNotFoundError):
        asyncio.run(
            expense_service.update_expense(
                _session(),
                _membership(TripRole.HOST),
                uuid.uuid4(),
                ExpenseUpdate(description="x"),
            )
        )


def test_read_shows_what_each_participant_owes() -> None:
    read = expense_service._read(_expense())  # ruff: ignore[private-member-access]
    assert [p.amount for p in read.participants] == [Decimal(71), Decimal(71)]


BODY: dict[str, Any] = {
    "payer_profile_id": str(KASIA),
    "amount": "142.00",
    "spent_on": "2026-11-07",
    "participants": [{"profile_id": str(KASIA)}, {"profile_id": str(TOMEK)}],
}


def _client(
    monkeypatch: pytest.MonkeyPatch,
    role: TripRole = TripRole.MEMBER,
    *,
    expense: Expense | None = None,
    grants: tuple[Grant, ...] | None = None,
) -> TestClient:
    _patch_service(monkeypatch, expense)

    def get_membership(
        _session: object, _trip: uuid.UUID, sub: str, min_role: TripRole
    ) -> TripMembership:
        if not role.satisfies(min_role):
            raise trip_service.TripRoleError(min_role)
        return _membership(role, sub)

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=get_membership)
    )
    app = create_app()
    if grants is None:
        authorize(app, OTHER)
    else:
        authorize(app, OTHER, grants)
    app.dependency_overrides[get_session] = _session
    return TestClient(app)


def test_post_answers_201_with_the_author(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)

    def commit_and_refresh(_session: object, expense: Expense) -> None:
        expense.id = uuid.uuid4()
        expense.created_at = datetime(2026, 11, 7, 20, 0, tzinfo=UTC)

    monkeypatch.setattr(db, "insert_expense", AsyncMock(side_effect=commit_and_refresh))
    response = client.post(path("create_expense", trip_id=TRIP), json=BODY)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created_by_sub"] == OTHER.sub
    assert body["currency"] == "PLN"
    assert body["amount"] == "142.00"
    assert [p["amount"] for p in body["participants"]] == ["71", "71"] or [
        Decimal(p["amount"]) for p in body["participants"]
    ] == [Decimal(71), Decimal(71)]


def test_post_percent_not_100_answers_422_with_a_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    body = BODY | {
        "split_method": "percent",
        "participants": [
            {"profile_id": str(p), "value": v}
            for p, v in ((KASIA, "50"), (ANIA, "30"), (TOMEK, "10"))
        ],
    }
    response = client.post(path("create_expense", trip_id=TRIP), json=body)
    assert response.status_code == 422
    (item,) = response.json()["detail"]
    assert item["type"] == "expense.percent_sum"
    assert item["loc"] == ["body", "participants"]


def test_delete_someone_elses_expense_answers_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, expense=_expense())
    response = client.delete(
        path("delete_expense", trip_id=TRIP, expense_id=uuid.uuid4())
    )
    assert response.status_code == 403


def test_host_deletes_with_204_and_unknown_id_is_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, TripRole.HOST, expense=_expense())
    url = path("delete_expense", trip_id=TRIP, expense_id=uuid.uuid4())
    assert client.delete(url).status_code == 204
    missing = _client(monkeypatch, TripRole.HOST, expense=None)
    assert missing.delete(url).status_code == 404


def test_outsider_gets_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(side_effect=trip_service.TripNotFoundError("x")),
    )
    assert client.get(path("list_expenses", trip_id=TRIP)).status_code == 404


def test_list_is_a_page_and_passes_the_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    select = AsyncMock(
        return_value=Page[Any](items=[_expense()], total=1, page=1, size=20, pages=1)
    )
    monkeypatch.setattr(db, "select_page", select)
    response = client.get(
        path("list_expenses", trip_id=TRIP),
        params={
            "date_from": "2026-11-01",
            "payer_profile_id": str(KASIA),
            "participant_profile_id": str(TOMEK),
            "sort": "amount",
            "dir": "asc",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert select.await_args is not None
    query = select.await_args.args[2]
    assert (query.date_from, query.payer_profile_id, query.dir) == (
        date(2026, 11, 1),
        KASIA,
        "asc",
    )
    assert (
        client.get(
            path("list_expenses", trip_id=TRIP), params={"sort": "bogus"}
        ).status_code
        == 422
    )


def test_writes_need_the_write_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    read_only = (Grant(Feature.EXPENSES_CORE, Access.READ),)
    client = _client(monkeypatch, grants=read_only)
    assert (
        client.post(path("create_expense", trip_id=TRIP), json=BODY).status_code == 403
    )
    monkeypatch.setattr(
        db,
        "select_page",
        AsyncMock(return_value=Page[Any](items=[], total=0, page=1, size=20, pages=0)),
    )
    assert client.get(path("list_expenses", trip_id=TRIP)).status_code == 200


def test_editing_after_the_trip_currency_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_service(monkeypatch, _expense())
    monkeypatch.setattr(trip_service, "get_trip", AsyncMock(return_value=_trip("EUR")))
    host = _membership(TripRole.HOST)

    async def update(data: ExpenseUpdate) -> object:
        return await expense_service.update_expense(
            _session(), host, uuid.uuid4(), data
        )

    asyncio.run(update(ExpenseUpdate(description="Obiad")))  # no currency check
    with pytest.raises(expense_service.ExpenseInvalidError) as caught:
        asyncio.run(update(ExpenseUpdate(amount=Decimal(10))))
    assert [v.code for v in caught.value.violations] == [
        ExpenseErrorCode.CURRENCY_MISMATCH
    ]
