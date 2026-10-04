"""What MCP tools do: thin calls into the services of other domains."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.mcp.schemas import WhoAmI
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import get_sessionmaker
from tuttitrip.shared.pagination.schemas import Page, PageParams
from tuttitrip.shared.permissions.logic.resolution import EffectivePermissions, resolve
from tuttitrip.shared.permissions.services import permission_service
from tuttitrip.trips.schemas import TripListQuery, TripRead, TripRole
from tuttitrip.trips.services import trip_service


@asynccontextmanager
async def open_session() -> AsyncGenerator[AsyncSession]:
    """Open a database session for one tool call or permission lookup.

    Yields:
        An async session, closed afterwards.
    """
    async with get_sessionmaker()() as session:
        yield session


async def load_permissions(
    session: AsyncSession, user: AuthenticatedUser
) -> EffectivePermissions:
    """Resolve the caller's permissions (the Auth0 admin claim skips the query).

    Args:
        session: Open session.
        user: The caller.

    Returns:
        Effective permissions, never read from the token itself.
    """
    grants = (
        [] if user.is_admin else await permission_service.load_grants(session, user.sub)
    )
    return resolve(grants, superadmin=user.is_admin)


def whoami(user: AuthenticatedUser, permissions: EffectivePermissions) -> WhoAmI:
    """Describe the caller.

    Args:
        user: The caller.
        permissions: Their effective permissions.

    Returns:
        Identity and the permission map.
    """
    return WhoAmI(
        sub=user.sub,
        roles=user.roles,
        is_admin=user.is_admin,
        access=permissions.as_dict(),
    )


async def list_trips(
    session: AsyncSession, user: AuthenticatedUser, params: PageParams
) -> Page[TripRead]:
    """One page of the caller's trips, newest first.

    Args:
        session: Open session.
        user: The caller.
        params: Page and size.

    Returns:
        The page with the total.
    """
    query = TripListQuery(page=params.page, size=params.size)
    return await trip_service.list_trips(session, user.sub, query)


async def get_trip(
    session: AsyncSession, user: AuthenticatedUser, trip_id: UUID
) -> TripRead:
    """Read one trip the caller is a member of.

    Args:
        session: Open session.
        user: The caller.
        trip_id: Trip id.

    Returns:
        The trip with the caller's role.

    Raises:
        TripNotFoundError: Unknown trip or the caller is not on it.
    """
    membership = await trip_service.get_membership(
        session, trip_id, user.sub, TripRole.MEMBER
    )
    return await trip_service.get_trip(session, membership)
