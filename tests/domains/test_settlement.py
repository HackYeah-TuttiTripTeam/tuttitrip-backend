"""Settlement: balances sum to zero and credit the payer."""

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tuttitrip.expenses.settlement.logic.balances import net_balances
from tuttitrip.expenses.settlement.schemas import Payment
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser


def test_equal_split_balances() -> None:
    balances = net_balances(
        [
            Payment(
                payer="Kasia",
                amount=Decimal(150),
                participants=["Kasia", "Tomek", "Ola"],
            )
        ]
    )
    assert balances == {
        "Kasia": Decimal(100),
        "Tomek": Decimal(-50),
        "Ola": Decimal(-50),
    }
    assert sum(balances.values()) == 0


def test_balances_endpoint() -> None:
    app = create_app()
    authorize(app, AuthenticatedUser(sub="auth0|tester"))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/expenses/settlement/balances",
            json={
                "payments": [{"payer": "A", "amount": "10", "participants": ["A", "B"]}]
            },
        )
    assert response.status_code == 200
    assert {k: Decimal(v) for k, v in response.json()["balances"].items()} == {
        "A": Decimal(5),
        "B": Decimal(-5),
    }
