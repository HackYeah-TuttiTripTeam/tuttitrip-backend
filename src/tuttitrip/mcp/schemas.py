"""DTOs returned by MCP tools."""

from datetime import datetime
from typing import Final
from uuid import UUID

from pydantic import BaseModel, Field

from tuttitrip.planning.plans.schemas import (
    FloorMiss,
    PlanBudget,
    PlanConflict,
    PlanDay,
    PlanFairness,
    PlanLodging,
)
from tuttitrip.profiles.feedback.schemas import VetoRead
from tuttitrip.shared.permissions.registry import Access

TRIP_NOT_FOUND: Final = "Nie znaleziono podróży albo brak dostępu"
"""The one message for an unknown trip and for a trip the caller is not on."""
NO_PLAN: Final = "Ta podróż nie ma jeszcze planu"
"""The trip exists but no plan was generated."""
PLAN_NOT_POSSIBLE: Final = (
    "Podróż nie ma jeszcze dat, miasta albo osób potrzebnych do planu"
)
"""A plan cannot be computed (``PlanInputError``)."""
DAY_OUT_OF_RANGE: Final = "Plan nie ma takiego dnia"
"""``get_plan`` was asked for a day the plan does not have."""
NOT_ALLOWED: Final = "Tylko host lub co-host może działać w imieniu innej osoby"
"""A member tried to act for somebody else's profile."""
PROFILE_NOT_FOUND: Final = "Nie znaleziono osoby w tej podróży"
"""No profile of the caller (or the one named) on the trip."""
PLACE_NOT_FOUND: Final = "Nie znaleziono miejsca"
"""The place is not in the catalog."""
RATE_LIMITED: Final = "Za dużo zmian w krótkim czasie, spróbuj za minutę"
"""The caller used all write calls of the minute."""
VETO_EXISTS: Final = "Ta osoba ma już weto na to miejsce"
"""A second veto on the same place."""


class WhoAmI(BaseModel):
    """The caller as the MCP server sees them."""

    sub: str = Field(description="Auth0 user id.")
    roles: list[str] = Field(description="Roles from the Auth0 roles claim.")
    is_admin: bool = Field(description="Whether the caller is a TuttiTrip admin.")
    access: dict[str, Access] = Field(
        description="Feature code -> READ or WRITE, as resolved for this request."
    )


class McpPlan(BaseModel):
    """A stored plan, day by day, for a chat client.

    Without ``explain``, ``verdicts`` and telemetry: those are for the app.
    """

    trip_id: UUID
    version: int = Field(ge=1)
    plan_hash: str = Field(description="12 hex characters; equal for equal input.")
    created_at: datetime
    day_count: int = Field(ge=0, description="Days in the whole plan.")
    days: list[PlanDay] = Field(
        description="The requested day, or all days; hours, price, source, verified."
    )
    lodging: PlanLodging | None = None
    budget: PlanBudget
    conflicts: list[PlanConflict]


class McpFairness(BaseModel):
    """The fairness ledger of the newest plan (docs/algorytm.md, sections 3 and 10).

    Names and numbers only: no health limits and no e-mail addresses.
    """

    trip_id: UUID
    version: int = Field(ge=1)
    plan_hash: str
    fairness: PlanFairness = Field(
        description="`min_r`, `jain`, and per person `u`, `u_star`, `r`, `floor_eff` "
        "and the five domains `q`. For one person read the domains, not `jain`."
    )
    floors_missed: list[FloorMiss]
    conflicts: list[PlanConflict]


class VetoResult(BaseModel):
    """A filed veto and what it did to the plan."""

    veto: VetoRead
    plan_version: int | None = Field(
        description="Version of the recomputed plan; null when none could be made."
    )
    plan_hash: str | None = None
    removed: list[str] = Field(description="Places that left the plan.")
    added: list[str] = Field(description="Places that came into the plan instead.")
