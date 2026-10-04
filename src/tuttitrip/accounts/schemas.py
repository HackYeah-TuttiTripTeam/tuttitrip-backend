"""DTOs of ``PATCH /me/account``."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

MAX_NAME_CHARS = 100
# C0 controls, DEL, zero-width marks (U+200B-U+200F), bidi embeddings and
# overrides (U+202A-U+202E) and bidi isolates (U+2066-U+2069).
_FORBIDDEN_CHARS = r"\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069"


class AccountErrorCode(StrEnum):
    """Stable code of an account error, sent as ``detail.code`` (map by it)."""

    PROVIDER_MANAGED = "account.provider_managed"


class AccountSource(StrEnum):
    """Login provider that owns the account's data (``email``: e-mail and password)."""

    EMAIL = "email"
    GOOGLE = "google"
    DISCORD = "discord"
    OTHER = "other"


AccountName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_NAME_CHARS,
        pattern=f"^[^{_FORBIDDEN_CHARS}]+$",
    ),
]


class AccountUpdate(BaseModel):
    """New data of the caller's account; any other field (e.g. ``sub``) is ignored."""

    name: AccountName = Field(
        description=(
            "Display name, 1 to 100 characters, without control, zero-width "
            "or bidi control characters."
        )
    )


class AccountRead(BaseModel):
    """The caller's account after the change."""

    sub: str = Field(description="Auth0 user id (always the token's account).")
    name: str | None = None
    email: str | None = None
    source: AccountSource


class ProviderManagedDetail(BaseModel):
    """Why the account cannot be changed in TuttiTrip.

    Clients map by ``code`` and ``source``; ``message`` is English text for
    developers, never shown to people as is.
    """

    code: Literal[AccountErrorCode.PROVIDER_MANAGED] = AccountErrorCode.PROVIDER_MANAGED
    source: AccountSource = Field(description="Provider that owns the data.")
    message: str = Field(description="For developers; clients map by code/source.")


class ProviderManagedError(BaseModel):
    """409 body: the account's data comes from Google, Discord or another provider."""

    detail: ProviderManagedDetail
