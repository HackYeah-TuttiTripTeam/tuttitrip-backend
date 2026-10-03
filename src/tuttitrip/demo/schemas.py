"""Demo login DTOs."""

from typing import Literal

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


class DemoResetResult(BaseModel):
    """What the internal reset did."""

    status: Literal["reset", "disabled"] = Field(
        description="`disabled` when the demo is switched off: nothing was touched."
    )
    trips: int = Field(default=0, description="Trips created for the demo account.")
