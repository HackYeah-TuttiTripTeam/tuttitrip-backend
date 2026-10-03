"""Auth DTOs."""

from pydantic import BaseModel, Field


class AuthenticatedUser(BaseModel):
    """Identity extracted from a verified Auth0 access token."""

    sub: str = Field(description="Auth0 user id, e.g. 'google-oauth2|123'.")
    scopes: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class MeResponse(BaseModel):
    """Response of ``GET /me``."""

    sub: str
    scopes: list[str]
    permissions: list[str]
