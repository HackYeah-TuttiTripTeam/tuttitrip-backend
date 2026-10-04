"""DTOs of ``PATCH /me/account``."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

MAX_NAME_CHARS = 100
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
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
]


class AccountUpdate(BaseModel):
    """New data of the caller's account; any other field (e.g. ``sub``) is ignored."""

    name: AccountName = Field(description="Display name, 1 to 100 characters.")


class AccountRead(BaseModel):
    """The caller's account after the change."""

    sub: str = Field(description="Auth0 user id (always the token's account).")
    name: str | None = None
    email: str | None = None
    source: AccountSource


class ProviderManagedDetail(BaseModel):
    """Why the account cannot be changed in TuttiTrip."""

    code: Literal["account.provider_managed"] = PROVIDER_MANAGED
    source: AccountSource = Field(description="Provider that owns the data.")
    message: str


class ProviderManagedError(BaseModel):
    """409 body: the account's data comes from Google, Discord or another provider."""

    detail: ProviderManagedDetail
