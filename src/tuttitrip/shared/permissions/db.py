"""Queries on roles, role grants, user roles and user grants."""

from collections.abc import Iterable, Sequence

from sqlalchemy import delete, select, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.permissions.models import (
    PermissionAudit,
    Role,
    RoleGrant,
    UserGrant,
    UserRole,
)
from tuttitrip.shared.permissions.registry import Access


async def select_effective_grants(
    session: AsyncSession, sub: str, *, default_role: str, claim_only_role: str
) -> Sequence[tuple[str, Access]]:
    """Every grant that applies to a user, in one round trip.

    Args:
        session: Open session.
        sub: Auth0 subject.
        default_role: Role every user has without an assignment.
        claim_only_role: Role that a database row never confers (superadmin).

    Returns:
        ``(feature, level)`` pairs from the user's roles, the default role and
        direct grants (duplicates possible).
    """
    assigned = select(UserRole.role_name).where(UserRole.user_sub == sub)
    from_roles = select(RoleGrant.feature, RoleGrant.level).where(
        (RoleGrant.role_name == default_role) | RoleGrant.role_name.in_(assigned),
        RoleGrant.role_name != claim_only_role,
    )
    direct = select(UserGrant.feature, UserGrant.level).where(UserGrant.user_sub == sub)
    result = await session.execute(union_all(from_roles, direct))
    return [(row[0], Access(row[1])) for row in result.all()]


async def select_roles(session: AsyncSession) -> Sequence[Role]:
    """All roles by name.

    Args:
        session: Open session.

    Returns:
        The roles.
    """
    return (await session.scalars(select(Role).order_by(Role.name))).all()


async def select_role(session: AsyncSession, name: str) -> Role | None:
    """One role.

    Args:
        session: Open session.
        name: Role name.

    Returns:
        The role, or None.
    """
    return await session.get(Role, name)


async def select_role_grants(
    session: AsyncSession, names: Iterable[str]
) -> Sequence[RoleGrant]:
    """Grants of the given roles.

    Args:
        session: Open session.
        names: Role names.

    Returns:
        Their grants, ordered by role and feature.
    """
    return (
        await session.scalars(
            select(RoleGrant)
            .where(RoleGrant.role_name.in_(list(names)))
            .order_by(RoleGrant.role_name, RoleGrant.feature)
        )
    ).all()


async def insert_role(session: AsyncSession, name: str, description: str) -> Role:
    """Add a role (caller commits).

    Args:
        session: Open session.
        name: Unique role name.
        description: Human-readable description.

    Returns:
        The new role.
    """
    role = Role(name=name, description=description, is_system=False)
    session.add(role)
    await session.flush()
    await session.refresh(role)
    return role


async def replace_role_grants(
    session: AsyncSession, name: str, grants: Iterable[tuple[str, Access]]
) -> None:
    """Replace every grant of a role (caller commits).

    Args:
        session: Open session.
        name: Role name.
        grants: New ``(feature, level)`` pairs, one per feature.
    """
    await session.execute(delete(RoleGrant).where(RoleGrant.role_name == name))
    session.add_all(
        RoleGrant(role_name=name, feature=feature, level=level)
        for feature, level in grants
    )
    await session.flush()


async def delete_role(session: AsyncSession, name: str) -> None:
    """Delete a role; its grants and assignments cascade (caller commits).

    Args:
        session: Open session.
        name: Role name.
    """
    await session.execute(delete(Role).where(Role.name == name))


async def select_user_role_names(session: AsyncSession, sub: str) -> list[str]:
    """Roles explicitly assigned to a user.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        Role names, sorted.
    """
    names = await session.scalars(
        select(UserRole.role_name)
        .where(UserRole.user_sub == sub)
        .order_by(UserRole.role_name)
    )
    return list(names.all())


async def select_user_grants(session: AsyncSession, sub: str) -> Sequence[UserGrant]:
    """Direct grants of a user.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        The grants, ordered by feature.
    """
    return (
        await session.scalars(
            select(UserGrant)
            .where(UserGrant.user_sub == sub)
            .order_by(UserGrant.feature)
        )
    ).all()


async def select_subjects(session: AsyncSession) -> list[str]:
    """Users with at least one assigned role or direct grant.

    Args:
        session: Open session.

    Returns:
        Auth0 subjects, sorted.
    """
    subs = select(UserRole.user_sub).union(select(UserGrant.user_sub)).subquery()
    result = await session.scalars(select(subs.c.user_sub).order_by(subs.c.user_sub))
    return list(result.all())


async def upsert_user_role(
    session: AsyncSession, sub: str, role: str, granted_by: str
) -> None:
    """Assign a role (idempotent; caller commits).

    Args:
        session: Open session.
        sub: Auth0 subject of the user.
        role: Role name.
        granted_by: Auth0 subject of the admin.
    """
    await session.execute(
        insert(UserRole)
        .values(user_sub=sub, role_name=role, granted_by=granted_by)
        .on_conflict_do_nothing()
    )


async def delete_user_role(session: AsyncSession, sub: str, role: str) -> None:
    """Revoke a role (caller commits).

    Args:
        session: Open session.
        sub: Auth0 subject.
        role: Role name.
    """
    await session.execute(
        delete(UserRole).where(UserRole.user_sub == sub, UserRole.role_name == role)
    )


async def upsert_user_grant(
    session: AsyncSession, sub: str, grant: tuple[str, Access], granted_by: str
) -> None:
    """Set the level of a direct grant (caller commits).

    Args:
        session: Open session.
        sub: Auth0 subject of the user.
        grant: ``(feature code, level)``.
        granted_by: Auth0 subject of the admin.
    """
    feature, level = grant
    statement = insert(UserGrant).values(
        user_sub=sub, feature=feature, level=level, granted_by=granted_by
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[UserGrant.user_sub, UserGrant.feature],
            set_={
                "level": statement.excluded.level,
                "granted_by": statement.excluded.granted_by,
                "granted_at": statement.excluded.granted_at,
            },
        )
    )


async def delete_user_grant(session: AsyncSession, sub: str, feature: str) -> None:
    """Revoke a direct grant (caller commits).

    Args:
        session: Open session.
        sub: Auth0 subject.
        feature: Feature code.
    """
    await session.execute(
        delete(UserGrant).where(UserGrant.user_sub == sub, UserGrant.feature == feature)
    )


def add_audit(session: AsyncSession, entry: PermissionAudit) -> None:
    """Record a change in the audit log (flushed with the caller's commit).

    Args:
        session: Open session.
        entry: The log entry.
    """
    session.add(entry)


async def select_audit(
    session: AsyncSession, *, target_sub: str | None, limit: int
) -> Sequence[PermissionAudit]:
    """Newest audit entries.

    Args:
        session: Open session.
        target_sub: Only entries about this user, if given.
        limit: Maximum number of entries.

    Returns:
        Entries, newest first.
    """
    statement = select(PermissionAudit).order_by(PermissionAudit.id.desc()).limit(limit)
    if target_sub is not None:
        statement = statement.where(PermissionAudit.target_sub == target_sub)
    return (await session.scalars(statement)).all()
