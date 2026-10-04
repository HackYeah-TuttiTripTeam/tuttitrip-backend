"""Interview DTOs."""

from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tuttitrip.profiles.preferences.schemas import PreferencesRead
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.shared.pagination.schemas import ListFilters, Page, PageParams
from tuttitrip.trips.schemas import TripRead

MAX_SDP_CHARS: Final = 100_000
"""Longest SDP offer the voice endpoint accepts, in characters."""


class CardKind(StrEnum):
    """UI card the web client renders for a question (the cards of plan.md)."""

    FAMILY_BUILDER = "family_builder"
    SLIDER = "slider"
    REQUIREMENT_TOGGLES = "requirement_toggles"
    SWIPE = "swipe"
    DOT_POOL = "dot_pool"
    BUDGET_RANGE = "budget_range"
    CHOICE = "choice"
    CONFIRM = "confirm"


class QuestionField(StrEnum):
    """What a question is about; finer than ``KnowledgeField``."""

    DESTINATION = "destination"
    DATES = "dates"
    PEOPLE = "people"
    BUDGET = "budget"
    PACE = "pace"
    IMPORTANCE = "importance"
    REQUIREMENTS = "requirements"
    INTERESTS = "interests"
    DIET = "diet"


class QuestionKey(BaseModel):
    """One question to one person, or to the group when ``person_id`` is empty."""

    model_config = ConfigDict(frozen=True)

    field: QuestionField
    person_id: UUID | None = None


class NextQuestion(QuestionKey):
    """The next thing to ask, chosen by code from the missing data.

    ``options`` come from the logic or the catalog, never from the model. An empty
    list on a ``choice`` card means a free answer.
    """

    card_kind: CardKind
    options: list[str] = Field(default_factory=list)
    impact: float | None = Field(
        default=None,
        description=(
            "How much the answer changes the plan (see `informativeness`); null "
            "when it was not measured and the fixed order decided."
        ),
    )


class ShownCard(BaseModel):
    """The card the assistant put on screen (``show_card`` of the AG-UI state).

    The host's answer is not a tool result: the client sends it as the text of
    the next user message (a JSON object is fine; the assistant reads it).
    """

    kind: CardKind
    question: str = Field(min_length=1)
    field: QuestionField | None = None
    person_id: UUID | None = None
    options: list[str] = Field(default_factory=list)


class ConstraintKind(StrEnum):
    """Yes/no access limits of a person (the flags of ``Constraints``)."""

    WHEELCHAIR = "wheelchair"
    STAIRS = "stairs"
    HEAT = "heat"
    COLD = "cold"
    AUDIO_DESCRIPTION = "audio_description"


class SessionStatus(StrEnum):
    """State of an interview session."""

    OPEN = "open"


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


class AssumptionCode(StrEnum):
    """What a preliminary plan had to assume; the UI may write its own text."""

    DATES = "dates"
    PEOPLE = "people"
    BUDGET = "budget"
    PREFERENCES = "preferences"


class Assumption(BaseModel):
    """One thing the preliminary plan assumed instead of data the host did not give."""

    code: AssumptionCode
    params: dict[str, str | int] = Field(
        default_factory=dict, description="Values for the UI's own wording."
    )
    text: str = Field(description="The assumption in Polish.")


class DraftPlanRead(BaseModel):
    """The preliminary plan built during the interview."""

    plan_id: UUID = Field(description="Read it with `GET /trips/{id}/plans/{plan_id}`.")
    version: int = Field(ge=1)
    plan_hash: str = Field(description="Same data and assumptions give the same hash.")
    assumptions: list[Assumption] = Field(
        description="What was assumed, so the host can correct it."
    )


class InterviewState(BaseModel):
    """The AG-UI shared state, sent as ``STATE_SNAPSHOT`` after the tools.

    Built by the server on every turn. The ``state`` of a client request is
    ignored.
    """

    knowledge: KnowledgeRead | None = Field(
        default=None, description='The "What we already know" panel.'
    )
    card: ShownCard | None = Field(default=None, description="The card to render.")
    draft_plan: DraftPlanRead | None = Field(
        default=None,
        description="The preliminary plan built this turn by `build_plan_now`.",
    )


class VoiceOffer(BaseModel):
    """The browser's WebRTC offer."""

    sdp: str = Field(min_length=1, max_length=MAX_SDP_CHARS, description="SDP offer.")


class VoiceAnswer(BaseModel):
    """The provider's answer; the server's sideband is already attached."""

    sdp: str = Field(description="SDP answer: set it as the remote description.")
    call_id: str = Field(description="Use it to hang up.")
