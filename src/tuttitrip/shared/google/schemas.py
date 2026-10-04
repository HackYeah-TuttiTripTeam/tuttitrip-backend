"""DTOs of the Google integration."""

from enum import StrEnum, unique
from typing import Literal

from pydantic import BaseModel, Field


@unique
class GoogleErrorCode(StrEnum):
    """Stable code of a Google access problem, sent as ``detail.code``."""

    NOT_CONNECTED = "google.not_connected"
    SCOPE_MISSING = "google.scope_missing"
    TOKEN_EXPIRED = "google.token_expired"  # ruff: ignore[hardcoded-password-string] error code
    UNAVAILABLE = "google.unavailable"


class GoogleErrorDetail(BaseModel):
    """What the user must do before Google can be used; clients map by ``code``."""

    code: Literal[
        GoogleErrorCode.NOT_CONNECTED,
        GoogleErrorCode.SCOPE_MISSING,
        GoogleErrorCode.TOKEN_EXPIRED,
    ]
    message: str = Field(description="For developers; clients map by code.")
    required_scope: str = Field(
        description="Google scope the sign-in with Google has to ask for."
    )
    fallback_url: str = Field(
        description="Path of the .ics file of the same plan, usable without Google."
    )


class GoogleErrorBody(BaseModel):
    """409 body: the user has to (re)connect Google."""

    detail: GoogleErrorDetail


class GoogleAccessError(Exception):
    """A Google call cannot be made or was refused; ``code`` says why."""

    def __init__(self, code: GoogleErrorCode, message: str) -> None:
        """Keep the code and a message without any token.

        Args:
            code: Stable error code.
            message: Short explanation, safe to log.
        """
        super().__init__(message)
        self.code = code
        self.message = message
