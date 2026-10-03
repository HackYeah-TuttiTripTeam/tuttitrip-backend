"""Interview DTOs."""

from enum import StrEnum

from pydantic import BaseModel, Field


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
