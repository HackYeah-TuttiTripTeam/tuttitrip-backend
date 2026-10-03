"""Place rating and veto endpoints (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from tuttitrip.profiles.feedback.schemas import (
    RatingRead,
    RatingUpdate,
    VetoCreate,
    VetoRead,
)
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.feedback.services.feedback_service import (
    FeedbackForbiddenError,
    FeedbackPlaceNotFoundError,
    ProfileNotFoundError,
    VetoExistsError,
    VetoNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember

router = APIRouter(prefix="/trips/{trip_id}", tags=["feedback"])


@router.put(
    "/profiles/{profile_id}/ratings/{place_id}",
    dependencies=[requires(Feature.PROFILES_FEEDBACK, Access.WRITE)],
)
async def rate_place(
    profile_id: UUID,
    place_id: UUID,
    data: RatingUpdate,
    membership: TripMember,
    session: SessionDep,
) -> RatingRead:
    """Rate a catalog place: want, neutral, or do not want with a reason.

    Your own profile, or any profile as a co-host or higher. Sending the same
    rating again keeps a single row.

    Args:
        profile_id: Whose rating it is.
        place_id: Catalog place.
        data: Value and, for ``dont_want``, the reason code.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The stored rating.
    """
    try:
        return await feedback_service.rate_place(
            session, membership, profile_id, place_id, data
        )
    except (ProfileNotFoundError, FeedbackPlaceNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _not_found(exc)) from exc
    except FeedbackForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.get("/ratings", dependencies=[requires(Feature.PROFILES_FEEDBACK, Access.READ)])
async def list_ratings(membership: TripMember, session: SessionDep) -> list[RatingRead]:
    """List every rating of the trip.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        All ratings.
    """
    return await feedback_service.list_ratings(session, membership)


@router.post(
    "/vetoes",
    status_code=status.HTTP_201_CREATED,
    dependencies=[requires(Feature.PROFILES_FEEDBACK, Access.WRITE)],
)
async def create_veto(
    data: VetoCreate, membership: TripMember, session: SessionDep
) -> VetoRead:
    """Veto a place for a person: a hard block, with the author recorded.

    Your own profile, or any profile as a co-host or higher (then
    ``on_behalf`` is true). The client should recompute the plan afterwards.

    Args:
        data: Whose veto and which place.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The veto.
    """
    try:
        return await feedback_service.create_veto(session, membership, data)
    except (ProfileNotFoundError, FeedbackPlaceNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _not_found(exc)) from exc
    except FeedbackForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except VetoExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/vetoes", dependencies=[requires(Feature.PROFILES_FEEDBACK, Access.READ)])
async def list_vetoes(membership: TripMember, session: SessionDep) -> list[VetoRead]:
    """List the vetoes in force on the trip (revoked ones are left out).

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        Active vetoes with their authors.
    """
    return await feedback_service.list_vetoes(session, membership)


@router.delete(
    "/vetoes/{veto_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[requires(Feature.PROFILES_FEEDBACK, Access.WRITE)],
)
async def revoke_veto(
    veto_id: UUID, membership: TripMember, session: SessionDep
) -> Response:
    """Revoke a veto: the place stops being blocked.

    Args:
        veto_id: Veto to revoke.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        An empty 204 response.
    """
    try:
        await feedback_service.revoke_veto(session, membership, veto_id)
    except VetoNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Veto not found") from exc
    except FeedbackForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _not_found(exc: Exception) -> str:
    return (
        "Profile not found"
        if isinstance(exc, ProfileNotFoundError)
        else "Place not found"
    )
