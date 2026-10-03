"""Trip endpoints and ``TripAccess``, the object-level guard for trip routes.

Feature permissions (``requires``) say what a user may do at all. ``TripAccess``
says what they may do on one trip: every route with ``{trip_id}`` in its path
depends on it (a test checks this), also in other domains, whose ``api.py``
may import it from here.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripCreate, TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError, TripRoleError

router = APIRouter(prefix="/trips", tags=["trips"])

TRIP_NOT_FOUND = "Trip not found"


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


@router.get("", dependencies=[requires(Feature.TRIPS_CORE, Access.READ)])
async def list_trips(user: CurrentUser, session: SessionDep) -> list[TripRead]:
    """List the trips the caller belongs to, with their role on each.

    Args:
        user: The authenticated caller.
        session: Database session.

    Returns:
        Trips, newest first.
    """
    return await trip_service.list_trips(session, user.sub)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[requires(Feature.TRIPS_CORE, Access.WRITE)],
)
async def create_trip(
    data: TripCreate, user: CurrentUser, session: SessionDep
) -> TripRead:
    """Create a trip; the caller becomes its host.

    Args:
        data: Trip payload.
        user: The authenticated organizer.
        session: Database session.

    Returns:
        The created trip.
    """
    return await trip_service.create_trip(session, user.sub, data)
