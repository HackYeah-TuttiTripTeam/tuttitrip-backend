"""DTOs returned by MCP tools."""

from pydantic import BaseModel, Field

from tuttitrip.shared.permissions.registry import Access


class WhoAmI(BaseModel):
    """The caller as the MCP server sees them."""

    sub: str = Field(description="Auth0 user id.")
    roles: list[str] = Field(description="Roles from the Auth0 roles claim.")
    is_admin: bool = Field(description="Whether the caller is a TuttiTrip admin.")
    access: dict[str, Access] = Field(
        description="Feature code -> READ or WRITE, as resolved for this request."
    )
