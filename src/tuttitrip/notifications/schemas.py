"""Notification DTOs: what a notification is, never how it reads.

The database keeps the ``type`` and the small ``params``; the frontend composes
the title and the text in the user's language. Actions are codes from a closed
set, so the backend stores no URLs or API paths: the frontend maps a code to a
route or a call.
"""

import uuid
from datetime import datetime
from enum import StrEnum, unique

from pydantic import BaseModel, ConfigDict, Field


@unique
class NotificationType(StrEnum):
    """What happened. Stored as plain text, so a new type needs no migration."""

    MEMBER_JOINED = "member_joined"
    VETO_ADDED = "veto_added"
    PROPOSAL_WAITING = "proposal_waiting"
    BUDGET_APPROVAL_WAITING = "budget_approval_waiting"
    PLAN_READY = "plan_ready"


@unique
class NotificationActionCode(StrEnum):
    """What the user can do from a notification (the frontend maps it)."""

    OPEN_TRIP = "open_trip"
    OPEN_PEOPLE = "open_people"
    OPEN_PLAN = "open_plan"
    APPROVE_PROPOSAL = "approve_proposal"
    REJECT_PROPOSAL = "reject_proposal"
    APPROVE_BUDGET = "approve_budget"
    REJECT_BUDGET = "reject_budget"


class NotificationAction(BaseModel):
    """One button on a notification."""

    code: NotificationActionCode
    params: dict[str, str] = Field(
        default_factory=dict, description="Small values the action needs, e.g. ids."
    )


class NotificationRead(BaseModel):
    """A notification as the owner sees it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str = Field(description="A `NotificationType` value; open for new types.")
    trip_id: uuid.UUID | None
    params: dict[str, str] = Field(
        description="Small values for the text: names, ids, amounts as strings."
    )
    actions: list[NotificationAction]
    read_at: datetime | None
    created_at: datetime
