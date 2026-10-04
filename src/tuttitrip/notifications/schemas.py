"""Notification DTOs: what a notification is, never how it reads.

The database keeps the ``type`` and the small ``params``; the frontend composes
the title and the text in the user's language. Actions are codes from a closed
set, so the backend stores no URLs or API paths: the frontend maps a code to a
route or a call.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams, SortDir


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


class NotificationSort(StrEnum):
    """What the list can be sorted by (mapped to columns in ``db.py``)."""

    CREATED_AT = "created_at"
    TYPE = "type"


class NotificationFilter(ListFilters):
    """Which of the caller's notifications; shared by the list and bulk marking.

    Dates are ISO 8601 in UTC (a value without an offset is read as UTC); the
    frontend turns the user's local days into this range.
    """

    read: bool | None = Field(
        default=None, description="true: read, false: unread, absent: all."
    )
    type: Annotated[list[str] | None, Field(max_length=20)] = Field(
        default=None, description="Repeatable: any of these types."
    )
    trip_id: uuid.UUID | None = None
    created_from: datetime | None = Field(
        default=None, description="Created at or after this moment (inclusive)."
    )
    created_to: datetime | None = Field(
        default=None, description="Created before this moment (exclusive)."
    )

    @field_validator("created_from", "created_to", mode="after")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class NotificationQuery(PageParams, NotificationFilter):
    """Query of ``GET /notifications``: paging, sorting and the filters."""

    sort: NotificationSort = NotificationSort.CREATED_AT
    dir: Annotated[SortDir, Field(description="Sort direction.")] = SortDir.DESC


class UnreadCount(BaseModel):
    """How many notifications the caller has not read."""

    count: Annotated[int, Field(ge=0)]
