"""Interview DTOs."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tuttitrip.profiles.preferences.schemas import PreferencesRead
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.shared.pagination.schemas import ListFilters, Page, PageParams
from tuttitrip.trips.schemas import TripRead


class CardKind(StrEnum):
    """UI card the web client renders for a question."""

    CHOICE = "choice"
    SLIDER = "slider"
    DOT_POOL = "dot_pool"
    TEXT = "text"


class InterviewCard(BaseModel):
    """Next question to ask the organizer."""

    kind: CardKind
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)


class SessionStatus(StrEnum):
    """State of an interview session."""

    OPEN = "open"
    CLOSED = "closed"


class SessionRead(BaseModel):
    """An interview session of one trip."""

    id: UUID = Field(description="The AG-UI `threadId` of this interview.")
    trip_id: UUID
    status: SessionStatus
    created_by: str = Field(description="Auth0 subject of the host who started it.")
    created_at: datetime
    updated_at: datetime
    message_count: int = Field(
        ge=0, description="Messages the host can see (their questions and answers)."
    )


class MessageRole(StrEnum):
    """Who said a displayed message."""

    USER = "user"
    ASSISTANT = "assistant"


class DisplayMessage(BaseModel):
    """One message of the conversation as the host sees it.

    Tool calls, tool results and system prompts are stored in the history but
    never shown.
    """

    position: int = Field(
        ge=0, description="Order in the whole conversation, stable across filters."
    )
    role: MessageRole
    text: str
    timestamp: datetime | None = None


class MessageSort(StrEnum):
    """Sort keys of the displayed messages."""

    POSITION = "position"


class MessageFilters(ListFilters):
    """Filters of the displayed messages."""

    role: MessageRole | None = Field(default=None, description="Only this speaker.")


class MessagesQuery(PageParams, MessageFilters):
    """Query of `GET .../sessions/current`: the messages come as a page."""

    sort: MessageSort = MessageSort.POSITION


class InterviewSessionRead(SessionRead):
    """A session with one page of its conversation."""

    messages: Page[DisplayMessage]


class KnowledgeField(StrEnum):
    """A thing the assistant wants to know about the trip."""

    DESTINATION = "destination"
    DATES = "dates"
    BUDGET = "budget"
    PEOPLE = "people"
    PREFERENCES = "preferences"


class ValueSource(StrEnum):
    """Who set the value that is stored now."""

    ASSISTANT = "assistant"
    HOST = "host"


class FieldRef(BaseModel):
    """Points at one value: a trip field, or a person's profile or preferences."""

    model_config = ConfigDict(frozen=True)

    field: KnowledgeField
    profile_id: UUID | None = Field(
        default=None,
        description=(
            "Set for `people` and `preferences` of one person. For `people` it "
            "is empty only in `missing`: the group needs more members."
        ),
    )


class FieldSource(FieldRef):
    """A filled value with the party that set it."""

    source: ValueSource


class KnowledgeRead(BaseModel):
    """The "What we already know" panel: the trip data read from `trips` and `profiles`.

    This is not a copy. The host fixes values through the trips and profiles
    endpoints; this view always shows what they store now.
    """

    trip: TripRead = Field(description="Destination, dates, day window and budget.")
    people: list[ProfileRead]
    preferences: list[PreferencesRead] = Field(description="One entry per person.")
    missing: list[FieldRef] = Field(description="What the assistant still has to ask.")
    sources: list[FieldSource] = Field(
        description="Who set each filled value; a value the host changed is `host`."
    )
