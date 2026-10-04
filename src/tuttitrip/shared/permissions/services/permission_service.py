"""Load a user's grants and administer roles and user grants.

Rules enforced here (the HTTP layer only checks ``admin.permissions``):

* nobody hands out more than they hold (role grants, role assignment, direct
  grants are all checked against the actor's effective permissions);
* ``superadmin`` comes only from the Auth0 claim: it cannot be assigned,
  edited or deleted, and a stray database row for it is ignored;
* the default role ``user`` holds explicit leaves outside ``admin.*`` only;
* every change is written to ``permission_audit`` in the same transaction.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby, starmap
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.permissions import db
from tuttitrip.shared.permissions.logic.resolution import (
    EffectivePermissions,
    Grant,
    beyond_reach,
    invalid_for_default_role,
    resolve,
)
from tuttitrip.shared.permissions.models import PermissionAudit
from tuttitrip.shared.permissions.registry import (
    DEFAULT_ROLE,
    SUPERADMIN_ROLE,
    SYSTEM_ROLES,
    Access,
    Feature,
    children,
)
from tuttitrip.shared.permissions.schemas import (
    AuditEntryRead,
    FeatureGrant,
    FeatureNode,
    RoleCreate,
    RoleRead,
    RoleUpdate,
    UserPermissionsRead,
)


@dataclass(frozen=True, slots=True)
class Actor:
    """The admin making a change."""

    sub: str
    permissions: EffectivePermissions


class RoleNotFoundError(Exception):
    """No role with this name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Role not found: {name}")


class RoleExistsError(Exception):
    """A role with this name already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Role already exists: {name}")


class ProtectedRoleError(Exception):
    """System roles cannot be deleted; ``superadmin`` cannot be changed or assigned."""


class InvalidGrantError(Exception):
    """The default role may only hold leaves outside ``admin.*``."""


class EscalationError(Exception):
    """The actor tried to grant more than they hold."""


def _fmt(grants: Iterable[Grant]) -> str:
    return ", ".join(f"{g.feature}:{g.level}" for g in grants)


def _deny_escalation(actor: Actor, grants: Iterable[Grant]) -> None:
    if excess := beyond_reach(actor.permissions, grants):
        msg = f"Cannot grant more than you hold: {_fmt(excess)}"
        raise EscalationError(msg)


def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    change: dict[str, Any],
    target: tuple[str | None, str | None],
) -> None:
    sub, role = target
    db.add_audit(
        session,
        PermissionAudit(
            actor_sub=actor.sub,
            action=action,
            change=change,
            target_sub=sub,
            target_role=role,
        ),
    )


async def load_access(session: AsyncSession, sub: str) -> tuple[list[Grant], bool]:
    """Every grant that applies to a user, and whether the account is blocked.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        The grants (codes not in the registry are kept; resolution skips them)
        and the blocked flag.
    """
    rows, blocked = await db.select_effective_grants(
        session, sub, default_role=DEFAULT_ROLE, claim_only_role=SUPERADMIN_ROLE
    )
    return list(starmap(Grant, rows)), blocked


async def load_grants(session: AsyncSession, sub: str) -> list[Grant]:
    """Every grant that applies to a user (roles, default role, direct).

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        The grants, whether or not the account is blocked.
    """
    return (await load_access(session, sub))[0]


async def block_account(
    session: AsyncSession,
    actor_sub: str,
    sub: str,
    *,
    deleted: bool = False,
    change: dict[str, int] | None = None,
) -> None:
    """Refuse the account in the API from now on, audit it and commit.

    A deleted account also loses its roles and direct grants.

    Args:
        session: Open session.
        actor_sub: Auth0 subject of the admin.
        sub: Auth0 subject of the account.
        deleted: Whether the account was deleted in Auth0.
        change: Counts of what a deletion cleaned up, for the audit entry.
    """
    await db.upsert_account_block(session, sub, deleted=deleted, blocked_by=actor_sub)
    if deleted:
        await db.delete_user_access(session, sub)
    db.add_audit(
        session,
        PermissionAudit(
            actor_sub=actor_sub,
            action="user.delete" if deleted else "user.block",
            change=change or {},
            target_sub=sub,
        ),
    )
    await session.commit()


async def revoke_issued_tokens(session: AsyncSession, sub: str) -> dict[str, int]:
    """Revoke the profile access tokens the account issued (caller commits).

    Args:
        session: Open session.
        sub: Auth0 subject of the issuer.

    Returns:
        ``access_tokens_revoked``.
    """
    revoked = await db.revoke_tokens_created_by(session, sub, datetime.now(UTC))
    return {"access_tokens_revoked": revoked}


async def is_blocked(session: AsyncSession, sub: str) -> bool:
    """Tell whether the account is blocked or deleted.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        True when a block row exists.
    """
    return await db.select_account_block(session, sub) is not None


async def unblock_account(session: AsyncSession, actor_sub: str, sub: str) -> None:
    """Lift the block, audit it and commit.

    Args:
        session: Open session.
        actor_sub: Auth0 subject of the admin.
        sub: Auth0 subject of the account.
    """
    await db.delete_account_block(session, sub)
    db.add_audit(
        session,
        PermissionAudit(
            actor_sub=actor_sub, action="user.unblock", change={}, target_sub=sub
        ),
    )
    await session.commit()


def feature_tree(node: Feature = Feature.ROOT) -> FeatureNode:
    """The registry as a tree.

    Args:
        node: Subtree root.

    Returns:
        The node with its descendants.
    """
    return FeatureNode(
        code=node,
        description=node.description,
        children=[feature_tree(child) for child in children(node)],
    )


def _dedupe(grants: list[FeatureGrant]) -> list[Grant]:
    # One level per feature: the highest wins if a payload repeats a feature.
    best: dict[Feature, Access] = {}
    for grant in grants:
        best[grant.feature] = max(grant.level, best.get(grant.feature, grant.level))
    return [Grant(feature.value, level) for feature, level in sorted(best.items())]


def _as_json(grants: Iterable[Grant]) -> list[dict[str, str]]:
    return [{"feature": g.feature, "level": g.level.value} for g in grants]


async def list_roles(session: AsyncSession) -> list[RoleRead]:
    """All roles with their grants.

    Args:
        session: Open session.

    Returns:
        Roles by name.
    """
    roles = await db.select_roles(session)
    grants = await db.select_role_grants(session, [r.name for r in roles])
    by_role = {
        name: [
            FeatureGrant(feature=Feature(g.feature), level=g.level)
            for g in group
            if g.feature in Feature
        ]
        for name, group in groupby(grants, key=lambda g: g.role_name)
    }
    return [
        RoleRead(
            name=r.name,
            description=r.description,
            is_system=r.is_system,
            grants=by_role.get(r.name, []),
        )
        for r in roles
    ]


async def _read_role(session: AsyncSession, name: str) -> RoleRead:
    for role in await list_roles(session):
        if role.name == name:
            return role
    raise RoleNotFoundError(name)


async def _role_grants(session: AsyncSession, name: str) -> list[Grant]:
    return [
        Grant(g.feature, g.level) for g in await db.select_role_grants(session, [name])
    ]


async def create_role(
    session: AsyncSession, actor: Actor, data: RoleCreate
) -> RoleRead:
    """Create a role with grants, audit it and commit.

    Args:
        session: Open session.
        actor: The admin.
        data: Name, description and grants.

    Returns:
        The created role.
    """
    grants = _dedupe(data.grants)
    _deny_escalation(actor, grants)
    if await db.select_role(session, data.name) is not None:
        raise RoleExistsError(data.name)
    await db.insert_role(session, data.name, data.description)
    await db.replace_role_grants(
        session, data.name, [(g.feature, g.level) for g in grants]
    )
    _audit(
        session,
        actor,
        "role.create",
        {"description": data.description, "grants": _as_json(grants)},
        target=(None, data.name),
    )
    await session.commit()
    return await _read_role(session, data.name)


async def update_role(
    session: AsyncSession, actor: Actor, name: str, data: RoleUpdate
) -> RoleRead:
    """Replace a role's description and grants, audit it and commit.

    The actor must hold every grant the role had and every grant it gets, so
    nobody can widen or narrow a role beyond their own reach.

    Args:
        session: Open session.
        actor: The admin.
        name: Role name.
        data: New description and the full list of grants.

    Returns:
        The updated role.
    """
    if name == SUPERADMIN_ROLE:
        msg = f"Role '{name}' is read-only (it comes from the Auth0 claim)"
        raise ProtectedRoleError(msg)
    role = await db.select_role(session, name)
    if role is None:
        raise RoleNotFoundError(name)
    grants = _dedupe(data.grants)
    if name == DEFAULT_ROLE and (invalid := invalid_for_default_role(grants)):
        msg = f"Role '{name}' may only hold leaves outside admin.*: {_fmt(invalid)}"
        raise InvalidGrantError(msg)
    before = await _role_grants(session, name)
    _deny_escalation(actor, [*before, *grants])
    role.description = data.description
    await db.replace_role_grants(session, name, [(g.feature, g.level) for g in grants])
    _audit(
        session,
        actor,
        "role.update",
        {
            "description": data.description,
            "before": _as_json(before),
            "after": _as_json(grants),
        },
        target=(None, name),
    )
    await session.commit()
    return await _read_role(session, name)


async def delete_role(session: AsyncSession, actor: Actor, name: str) -> None:
    """Delete a role (and its assignments), audit it and commit.

    Args:
        session: Open session.
        actor: The admin.
        name: Role name.
    """
    if name in SYSTEM_ROLES:
        msg = f"System role '{name}' cannot be deleted"
        raise ProtectedRoleError(msg)
    if await db.select_role(session, name) is None:
        raise RoleNotFoundError(name)
    before = await _role_grants(session, name)
    _deny_escalation(actor, before)
    await db.delete_role(session, name)
    _audit(
        session,
        actor,
        "role.delete",
        {"grants": _as_json(before)},
        target=(None, name),
    )
    await session.commit()


async def list_users(session: AsyncSession) -> list[str]:
    """Users with an assigned role or a direct grant.

    Args:
        session: Open session.

    Returns:
        Auth0 subjects.
    """
    return await db.select_subjects(session)


async def read_user(session: AsyncSession, sub: str) -> UserPermissionsRead:
    """A user's roles, direct grants and resulting effective levels.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        The user's permissions (the Auth0 claim is not known here).
    """
    roles = await db.select_user_role_names(session, sub)
    grants = await db.select_user_grants(session, sub)
    effective = resolve(await load_grants(session, sub))
    return UserPermissionsRead(
        sub=sub,
        roles=roles,
        grants=[
            FeatureGrant(feature=Feature(g.feature), level=g.level)
            for g in grants
            if g.feature in Feature
        ],
        effective=effective.as_dict(),
    )


async def assign_role(
    session: AsyncSession, actor: Actor, sub: str, role: str
) -> UserPermissionsRead:
    """Assign a role to a user, audit it and commit.

    Args:
        session: Open session.
        actor: The admin.
        sub: Auth0 subject of the user.
        role: Role name.

    Returns:
        The user's permissions afterwards.
    """
    if role == SUPERADMIN_ROLE:
        msg = f"Role '{role}' comes only from the Auth0 admin claim"
        raise ProtectedRoleError(msg)
    if await db.select_role(session, role) is None:
        raise RoleNotFoundError(role)
    _deny_escalation(actor, await _role_grants(session, role))
    await db.upsert_user_role(session, sub, role, actor.sub)
    _audit(
        session,
        actor,
        "user.role.assign",
        {},
        target=(sub, role),
    )
    await session.commit()
    return await read_user(session, sub)


async def revoke_role(
    session: AsyncSession, actor: Actor, sub: str, role: str
) -> UserPermissionsRead:
    """Remove a role from a user, audit it and commit (no-op if not assigned).

    Args:
        session: Open session.
        actor: The admin.
        sub: Auth0 subject of the user.
        role: Role name.

    Returns:
        The user's permissions afterwards.
    """
    await db.delete_user_role(session, sub, role)
    _audit(
        session,
        actor,
        "user.role.revoke",
        {},
        target=(sub, role),
    )
    await session.commit()
    return await read_user(session, sub)


async def set_grant(
    session: AsyncSession, actor: Actor, sub: str, feature: Feature, level: Access
) -> UserPermissionsRead:
    """Set a direct grant, audit it and commit.

    Args:
        session: Open session.
        actor: The admin.
        sub: Auth0 subject of the user.
        feature: Feature (and its subtree).
        level: Granted level.

    Returns:
        The user's permissions afterwards.
    """
    _deny_escalation(actor, [Grant(feature.value, level)])
    await db.upsert_user_grant(session, sub, (feature.value, level), actor.sub)
    _audit(
        session,
        actor,
        "user.grant.set",
        {"feature": feature.value, "level": level.value},
        target=(sub, None),
    )
    await session.commit()
    return await read_user(session, sub)


async def revoke_grant(
    session: AsyncSession, actor: Actor, sub: str, feature: Feature
) -> UserPermissionsRead:
    """Remove a direct grant, audit it and commit (no-op if absent).

    Args:
        session: Open session.
        actor: The admin.
        sub: Auth0 subject of the user.
        feature: Feature code.

    Returns:
        The user's permissions afterwards.
    """
    await db.delete_user_grant(session, sub, feature.value)
    _audit(
        session,
        actor,
        "user.grant.revoke",
        {"feature": feature.value},
        target=(sub, None),
    )
    await session.commit()
    return await read_user(session, sub)


async def list_audit(
    session: AsyncSession, *, target_sub: str | None, limit: int
) -> list[AuditEntryRead]:
    """Newest audit entries.

    Args:
        session: Open session.
        target_sub: Only entries about this user, if given.
        limit: Maximum number of entries.

    Returns:
        Entries, newest first.
    """
    entries = await db.select_audit(session, target_sub=target_sub, limit=limit)
    return [AuditEntryRead.model_validate(e) for e in entries]
