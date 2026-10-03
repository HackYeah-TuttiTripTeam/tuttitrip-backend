"""Permission DTOs: feature tree, roles, user assignments and ``GET /me``."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tuttitrip.shared.permissions.registry import Access, Feature

RoleName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,49}$", max_length=50)
]
EffectiveMap = Annotated[
    dict[str, Access],
    Field(
        description=(
            "Effective level per feature code, flattened (groups resolved). "
            "A missing code means no access."
        ),
        examples=[
            {
                Feature.TRIPS.value: Access.WRITE.value,
                Feature.TRIPS_CORE.value: Access.WRITE.value,
                Feature.SEARCH.value: Access.READ.value,
            }
        ],
    ),
]


class FeatureGrant(BaseModel):
    """``level`` on ``feature`` and everything below it."""

    model_config = ConfigDict(from_attributes=True)

    feature: Feature
    level: Access


class FeatureNode(BaseModel):
    """A node of the feature tree."""

    code: Feature
    description: str
    children: list[FeatureNode] = Field(default_factory=list)


class RoleRead(BaseModel):
    """A role with its grants."""

    name: str
    description: str
    is_system: bool = Field(description="Seeded role; cannot be deleted.")
    grants: list[FeatureGrant]


class RoleCreate(BaseModel):
    """Payload for a new role."""

    name: RoleName
    description: str = Field(default="", max_length=500)
    grants: list[FeatureGrant] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Replaces a role's description and all its grants."""

    description: str = Field(default="", max_length=500)
    grants: list[FeatureGrant]


class DirectGrantSet(BaseModel):
    """Level of a direct grant."""

    level: Access


class UserPermissionsRead(BaseModel):
    """A user's assignments as stored in the database."""

    sub: str
    roles: list[str] = Field(
        description="Assigned roles; the default role 'user' applies on top."
    )
    grants: list[FeatureGrant] = Field(description="Direct grants.")
    effective: EffectiveMap = Field(
        description="Effective levels from the database (without the Auth0 claim)."
    )


class AuditEntryRead(BaseModel):
    """One recorded change of roles or grants."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_sub: str
    action: str = Field(examples=["user.role.assign"])
    target_sub: str | None
    target_role: str | None
    change: dict[str, Any]
    created_at: datetime


class MeResponse(BaseModel):
    """Response of ``GET /me``."""

    sub: str
    scopes: list[str]
    permissions: list[str] = Field(
        description="Auth0 RBAC permissions from the token (not TuttiTrip features)."
    )
    roles: list[str] = Field(description="Roles from the Auth0 roles claim.")
    is_admin: bool = Field(description="True for TuttiTrip superadmins.")
    access: EffectiveMap
