"""Trip endpoints and ``TripAccess``, the object-level guard for trip routes.

Feature permissions (``requires``) say what a user may do at all. ``TripAccess``
says what they may do on one trip: every route with ``{trip_id}`` in its path
depends on it (a test checks this), also in other domains, whose ``api.py``
may import it from here.
"""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from tuttitrip.demo.services import sample_trip_service
from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import (
    MemberRead,
    MemberRoleUpdate,
    TripCreate,
    TripListQuery,
    TripMembership,
    TripRead,
    TripRole,
    TripUpdate,
    TripValidationErrors,
)
from tuttitrip.trips.services import member_service, trip_service
from tuttitrip.trips.services.member_service import (
    HostMustTransferError,
    MemberForbiddenError,
    MemberNotFoundError,
)
from tuttitrip.trips.services.trip_service import (
    TripInvalidError,
    TripNotFoundError,
    TripRoleError,
)

router = APIRouter(prefix="/trips", tags=["trips"])

TRIP_NOT_FOUND = "Trip not found"
MEMBER_NOT_FOUND = "Member not found"
HOST_MUST_TRANSFER = (
    "The host cannot leave: hand the host role to another member first "
    "(POST /trips/{trip_id}/members/{profile_id}/host)"
)


@dataclass(frozen=True, slots=True)
class TripAccess:
    """Dependency: the caller has at least ``min_role`` on ``{trip_id}``.

    404 when the trip does not exist or the caller is not on it (no leak of
    other people's trips), 403 when their trip role is too low.
    """

    min_role: TripRole

    async def __call__(
        self, trip_id: UUID, user: CurrentUser, session: SessionDep
    ) -> TripMembership:
        """Check the caller's role on the trip.

        Args:
            trip_id: Trip id from the path.
            user: The authenticated caller.
            session: Database session.

        Returns:
            The caller's membership (pass it to services as proof).
        """
        try:
            return await trip_service.get_membership(
                session, trip_id, user.sub, self.min_role
            )
        except TripNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, TRIP_NOT_FOUND) from exc
        except TripRoleError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


TripMember = Annotated[TripMembership, Depends(TripAccess(TripRole.MEMBER))]
TripCoHost = Annotated[TripMembership, Depends(TripAccess(TripRole.CO_HOST))]
TripHost = Annotated[TripMembership, Depends(TripAccess(TripRole.HOST))]


INVALID_TRIP: dict[int | str, dict[str, Any]] = {
    422: {"model": TripValidationErrors, "description": "A trip rule is broken."}
}


@router.get("", dependencies=[requires(Feature.TRIPS_CORE, Access.READ)])
async def list_trips(
    query: Annotated[TripListQuery, Query()],
    user: CurrentUser,
    session: SessionDep,
    accept_language: Annotated[str | None, Header()] = None,
) -> Page[TripRead]:
    """List a page of the caller's trips, with their role on each.

    Paged, filtered and sorted on the server: `page`, `size`, `sort`
    (`created_at` by default, `start_date`, `name`), `dir` (`desc` by default),
    and the filters `q`, `city`, `kind`, `start_from`, `start_to` and `role`
    (repeatable). Trips without `start_date` sort last in both directions.

    `when=past` is the history of the groups the caller was in, `when=upcoming`
    the trips still ahead (or without dates). `status=pending` finds the trips
    the caller was added to and has not confirmed yet.

    The first call of a new account also creates its sample trip ("Przykład: ...",
    `is_sample`), in Polish or English by `Accept-Language`; it comes back in this
    very list, the host can delete it and it is never created twice.

    Args:
        query: Paging, sort and filters.
        user: The authenticated caller.
        session: Database session.
        accept_language: Language of the sample trip for a new account.

    Returns:
        The page of trips.
    """
    await sample_trip_service.ensure_sample_trip(session, user.sub, accept_language)
    return await trip_service.list_trips(session, user.sub, query)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=INVALID_TRIP,
    dependencies=[requires(Feature.TRIPS_CORE, Access.WRITE)],
)
async def create_trip(
    data: TripCreate, user: CurrentUser, session: SessionDep
) -> TripRead:
    """Create a trip in one request; the caller becomes its host.

    Takes the same fields as the PATCH body (`name` is required); dates and
    each budget range must come in pairs. The trip, its host and the host's
    profile are created in one transaction.

    A broken rule answers 422 with a `TripErrorCode` in `type`.

    Args:
        data: Trip payload.
        user: The authenticated organizer.
        session: Database session.

    Returns:
        The created trip.
    """
    return await trip_service.create_trip(session, user.sub, data)


@router.get(
    "/{trip_id}",  # ruff: ignore[fast-api-unused-path-parameter] TripAccess reads it
    dependencies=[requires(Feature.TRIPS_CORE, Access.READ)],
)
async def get_trip(membership: TripMember, session: SessionDep) -> TripRead:
    """Read one trip: dates, kind, city, day window and budget.

    Args:
        membership: The caller's membership (any role).
        session: Database session.

    Returns:
        The trip.
    """
    try:
        return await trip_service.get_trip(session, membership)
    except TripNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, TRIP_NOT_FOUND) from exc


@router.patch(
    "/{trip_id}",  # ruff: ignore[fast-api-unused-path-parameter] TripAccess reads it
    responses=INVALID_TRIP,
    dependencies=[requires(Feature.TRIPS_CORE, Access.WRITE)],
)
async def update_trip(
    data: TripUpdate, membership: TripCoHost, session: SessionDep
) -> TripRead:
    """Change trip details (co-host or host); only sent fields change.

    A broken rule answers 422 with a `TripErrorCode` in `type`.

    Args:
        data: Fields to change.
        membership: The caller's membership (co-host or host).
        session: Database session.

    Returns:
        The updated trip.
    """
    try:
        return await trip_service.update_trip(session, membership, data)
    except TripNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, TRIP_NOT_FOUND) from exc
    except TripInvalidError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors) from exc


@router.delete(
    "/{trip_id}",  # ruff: ignore[fast-api-unused-path-parameter] TripAccess reads it
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[requires(Feature.TRIPS_CORE, Access.WRITE)],
)
async def delete_trip(membership: TripHost, session: SessionDep) -> None:
    """Delete the trip with its profiles, expenses and members (host only).

    Args:
        membership: The caller's membership (host).
        session: Database session.
    """
    await trip_service.delete_trip(session, membership)


@router.get(
    "/{trip_id}/members",  # ruff: ignore[fast-api-unused-path-parameter] TripAccess reads it
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.READ)],
)
async def list_members(membership: TripMember, session: SessionDep) -> list[MemberRead]:
    """List the trip's members with their roles (name from their profile).

    Args:
        membership: The caller's membership (any role).
        session: Database session.

    Returns:
        Members, highest role first.
    """
    return await member_service.list_members(session, membership)


@router.patch(
    "/{trip_id}/members/{profile_id}",  # ruff: ignore[fast-api-unused-path-parameter]
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE)],
)
async def update_member(
    profile_id: UUID,
    data: MemberRoleUpdate,
    membership: TripHost,
    session: SessionDep,
) -> MemberRead:
    """Make a member a co-host or a plain member (host only).

    Args:
        profile_id: Profile of the member.
        data: The new role.
        membership: The caller's membership (host).
        session: Database session.

    Returns:
        The member after the change.
    """
    try:
        return await member_service.set_role(session, membership, profile_id, data.role)
    except MemberNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, MEMBER_NOT_FOUND) from exc
    except MemberForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.delete(
    "/{trip_id}/members/{profile_id}",  # ruff: ignore[fast-api-unused-path-parameter]
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE)],
)
async def remove_member(
    profile_id: UUID, membership: TripCoHost, session: SessionDep
) -> None:
    """Remove a member; their profile stays on the trip without an account.

    A co-host removes members, the host removes members and co-hosts, nobody
    removes the host.

    Args:
        profile_id: Profile of the member.
        membership: The caller's membership (co-host or host).
        session: Database session.
    """
    try:
        await member_service.remove_member(session, membership, profile_id)
    except MemberNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, MEMBER_NOT_FOUND) from exc
    except MemberForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post(
    "/{trip_id}/members/{profile_id}/host",  # ruff: ignore[fast-api-unused-path-parameter]
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE)],
)
async def transfer_host(
    profile_id: UUID, membership: TripHost, session: SessionDep
) -> MemberRead:
    """Hand the host role to another member; the caller becomes a co-host.

    Needed before the host can leave the trip.

    Args:
        profile_id: Profile of the new host.
        membership: The caller's membership (host).
        session: Database session.

    Returns:
        The new host.
    """
    try:
        return await member_service.transfer_host(session, membership, profile_id)
    except MemberNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, MEMBER_NOT_FOUND) from exc
    except MemberForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post(
    "/{trip_id}/membership/confirm",  # ruff: ignore[fast-api-unused-path-parameter]
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE)],
)
async def confirm_membership(membership: TripMember, session: SessionDep) -> MemberRead:
    """Confirm the caller's participation; the host then sees `confirmed`.

    Idempotent. Any member may confirm, whatever their role.

    Args:
        membership: The caller's membership (any role).
        session: Database session.

    Returns:
        The caller as a member.
    """
    try:
        return await member_service.confirm(session, membership)
    except MemberNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, MEMBER_NOT_FOUND) from exc


@router.post(
    "/{trip_id}/membership/leave",  # ruff: ignore[fast-api-unused-path-parameter]
    status_code=status.HTTP_204_NO_CONTENT,
    responses={409: {"description": "The host cannot leave before handing over."}},
    dependencies=[requires(Feature.TRIPS_MEMBERS, Access.WRITE)],
)
async def leave_trip(membership: TripMember, session: SessionDep) -> None:
    """Leave the trip; the caller's profile stays without an account.

    The profile, expenses and balance stay in the trip and the profile can be
    claimed again from an invitation. The host answers 409 until they hand over
    the host role.

    Args:
        membership: The caller's membership (any role).
        session: Database session.
    """
    try:
        await member_service.leave(session, membership)
    except HostMustTransferError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, HOST_MUST_TRANSFER) from exc
