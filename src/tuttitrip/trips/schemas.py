"""Trip DTOs."""

from datetime import datetime
from enum import StrEnum, unique
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


@unique
class TripRole(StrEnum):
    """A person's role on one trip: ``member < co_host < host``.

    The creator is the host. Trip roles are object-level: they say what a
    user may do on *this* trip, on top of the global feature permissions.
    """

    MEMBER = "member"
    CO_HOST = "co_host"
    HOST = "host"

    @property
    def rank(self) -> int:
        """Position in the ordering (higher means more rights).

        Returns:
            0 for member, 1 for co-host, 2 for host.
        """
        return list(TripRole).index(self)

    def satisfies(self, required: TripRole) -> bool:
        """Whether this role is enough for ``required``.

        Args:
            required: The minimum role an operation needs.

        Returns:
            True when this role ranks at least as high.
        """
        return self.rank >= required.rank


class TripCreate(BaseModel):
    """Payload for creating a trip."""

    name: str = Field(min_length=1, max_length=200)
    destination: str | None = Field(default=None, max_length=200)


class TripRead(BaseModel):
    """A trip as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    destination: str | None
    created_at: datetime
    my_role: TripRole = Field(description="The caller's role on this trip.")


class TripMembership(BaseModel):
    """Proof that a user may act on a trip, as checked by ``TripAccess``."""

    trip_id: UUID
    sub: str
    role: TripRole
