"""Interview DTOs."""

from enum import StrEnum
from typing import Literal

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


class Fact(BaseModel):
    """One thing the agent knows about the trip (a row of "Co już wiem")."""

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: str


class ShownCard(BaseModel):
    """Card the client currently renders; ``confirm`` is the closing card."""

    kind: CardKind | Literal["confirm"]
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)


class InterviewState(BaseModel):
    """AG-UI shared state: the panel "Co już wiem" plus the current card."""

    facts: list[Fact] = Field(default_factory=list)
    card: ShownCard | None = None
