"""Settlement DTOs."""

from decimal import Decimal

from pydantic import BaseModel, Field


class Payment(BaseModel):
    """Who paid how much, and who shares the cost (split equally)."""

    payer: str = Field(min_length=1)
    amount: Decimal = Field(ge=0)
    participants: list[str] = Field(min_length=1)


class BalancesRequest(BaseModel):
    """Payments to settle."""

    payments: list[Payment]


class BalancesResponse(BaseModel):
    """Net balance per person: positive means they are owed money."""

    balances: dict[str, Decimal]
