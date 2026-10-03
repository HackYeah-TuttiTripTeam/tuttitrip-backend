"""Demo login DTOs."""

from pydantic import BaseModel, Field


class DemoSession(BaseModel):
    """Auth0 tokens of the demo account."""

    access_token: str
    expires_in: int = Field(description="Seconds until the access token expires.")
    token_type: str = "Bearer"  # ruff: ignore[hardcoded-password-string]  # the OAuth token type
    refresh_token: str | None = Field(
        default=None,
        description="Only when the deployment allows `offline_access`.",
    )
