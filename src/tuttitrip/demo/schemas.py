"""Demo login DTOs."""

from pydantic import BaseModel, Field


class DemoLoginRequest(BaseModel):
    """The demo token from the link's fragment (``/demo#t=<token>``)."""

    token: str = Field(min_length=1, max_length=512)


class DemoSession(BaseModel):
    """Auth0 tokens of the demo account."""

    access_token: str
    expires_in: int = Field(description="Seconds until the access token expires.")
    token_type: str = "Bearer"  # ruff: ignore[hardcoded-password-string]  # the OAuth token type, not a secret
    refresh_token: str | None = Field(
        default=None,
        description="Only when the deployment allows `offline_access`.",
    )
