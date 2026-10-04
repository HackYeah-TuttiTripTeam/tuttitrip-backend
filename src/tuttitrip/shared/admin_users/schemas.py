"""DTOs of ``GET /admin/users``: filters, sort and one Auth0 account."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from tuttitrip.shared.pagination.schemas import ListFilters, PageParams

MAX_QUERY_CHARS = 100


class UserSort(StrEnum):
    """Sortable fields (the ones Auth0 user search v3 can sort by)."""

    CREATED_AT = "created_at"
    LAST_LOGIN = "last_login"
    EMAIL = "email"


class UserFilters(ListFilters):
    """Filters of the user list."""

    q: Annotated[
        str | None,
        Field(
            max_length=MAX_QUERY_CHARS,
            description="Text found in the e-mail or name (case-insensitive).",
        ),
    ] = None
    blocked: Annotated[
        bool | None, Field(description="Only blocked (true) or active (false).")
    ] = None


class UserQuery(PageParams, UserFilters):
    """Query of ``GET /admin/users``."""

    sort: UserSort = UserSort.CREATED_AT


class AdminUserRead(BaseModel):
    """One Auth0 account, reduced to what an administrator needs."""

    sub: str = Field(description="Auth0 user id, the `sub` of the user's tokens.")
    email: str | None = None
    name: str | None = None
    provider: str | None = Field(
        default=None, description="Login provider, e.g. google-oauth2, discord, auth0."
    )
    last_login: datetime | None = None
    created_at: datetime | None = None
    blocked: bool = False
