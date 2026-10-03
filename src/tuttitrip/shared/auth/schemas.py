"""Auth DTOs."""

from pydantic import BaseModel, Field

ADMIN_ROLE = "admin"


class AuthenticatedUser(BaseModel):
    """Identity extracted from a verified Auth0 access token."""

    sub: str = Field(description="Auth0 user id, e.g. 'google-oauth2|123'.")
    scopes: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    roles: list[str] = Field(
        default_factory=list,
        description="Roles from the Auth0 roles claim, e.g. ['admin'].",
    )

    @property
    def is_admin(self) -> bool:
        """Whether the user is a TuttiTrip administrator.

        Returns:
            True when the roles claim contains ``admin``.
        """
        return ADMIN_ROLE in self.roles
