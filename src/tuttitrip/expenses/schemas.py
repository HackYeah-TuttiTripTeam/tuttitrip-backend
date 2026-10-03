"""Expense DTOs."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExpenseRead(BaseModel):
    """An expense as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trip_id: UUID
    payer_profile_id: UUID
    amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    description: str
    created_at: datetime
