"""Roles and grants. Users are Auth0 subjects (``sub``); there is no user table.

Every change is recorded in ``permission_audit`` in the same transaction; a
trigger (migration) makes that table append-only. Feature codes are plain
strings: the registry lives in code, so a stored code
of a removed feature is simply ignored by resolution. The worker role has no
grants on these tables (``deploy/worker-grants.sql``).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Identity,
    String,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base
from tuttitrip.shared.permissions.registry import Access

FEATURE_CODE_LENGTH = 100
SUB_LENGTH = 255


def _access_column() -> Enum:
    # VARCHAR + CHECK (``_level_check``) instead of a native enum: adding a
    # level stays a cheap migration.
    return Enum(Access, name="access_level", native_enum=False, length=10)


def _level_check() -> CheckConstraint:
    allowed = ", ".join(f"'{level.value}'" for level in Access)
    return CheckConstraint(f"level IN ({allowed})", name="level")


class Role(Base):
    """A named set of grants."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    # Seeded roles the code relies on (``user``, ``superadmin``): never deleted.
    is_system: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RoleGrant(Base):
    """``level`` on ``feature`` (and its subtree) for everyone with the role."""

    __tablename__ = "role_grants"
    __table_args__ = (_level_check(),)

    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True
    )
    feature: Mapped[str] = mapped_column(String(FEATURE_CODE_LENGTH), primary_key=True)
    level: Mapped[Access] = mapped_column(_access_column())


class UserRole(Base):
    """Role assigned to a user (the default role applies without a row)."""

    __tablename__ = "user_roles"

    user_sub: Mapped[str] = mapped_column(String(SUB_LENGTH), primary_key=True)
    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True, index=True
    )
    granted_by: Mapped[str | None] = mapped_column(String(SUB_LENGTH))
    granted_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UserGrant(Base):
    """A grant given directly to one user, outside any role."""

    __tablename__ = "user_grants"
    __table_args__ = (_level_check(),)

    user_sub: Mapped[str] = mapped_column(String(SUB_LENGTH), primary_key=True)
    feature: Mapped[str] = mapped_column(String(FEATURE_CODE_LENGTH), primary_key=True)
    level: Mapped[Access] = mapped_column(_access_column())
    granted_by: Mapped[str | None] = mapped_column(String(SUB_LENGTH))
    granted_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PermissionAudit(Base):
    """Append-only log of role and grant changes (who, what, when)."""

    __tablename__ = "permission_audit"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_sub: Mapped[str] = mapped_column(String(SUB_LENGTH))
    action: Mapped[str] = mapped_column(String(50))
    # No foreign keys: the log outlives deleted roles.
    target_sub: Mapped[str | None] = mapped_column(String(SUB_LENGTH), index=True)
    target_role: Mapped[str | None] = mapped_column(String(50))
    change: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
