"""Settlement DTOs."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class BalanceRead(BaseModel):
    """Net balance of one person: positive means they are owed money."""

    profile_id: UUID
    amount: Decimal = Field(description="In the trip's currency, to the cent.")


class TransferRead(BaseModel):
    """One payment that settles part of the trip."""

    from_profile_id: UUID
    to_profile_id: UUID
    amount: Decimal = Field(description="In the trip's currency, to the cent.")


class SettlementRead(BaseModel):
    """Balances and the smallest list of transfers of a trip."""

    currency: str | None
    total_spent: Decimal = Field(description="Sum of all expenses.")
    balances: list[BalanceRead] = Field(
        description="Every person on the trip, ordered by profile id; sums to 0.00."
    )
    transfers: list[TransferRead]
