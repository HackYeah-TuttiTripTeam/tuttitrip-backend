"""Profile endpoints (nested under a trip)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from tuttitrip.expenses.services import expense_service
from tuttitrip.profiles.logic.weight_presets import (
    FocusProfileRequiredError,
    WeightRatioError,
)
from tuttitrip.profiles.schemas import (
    AccessTokenCreate,
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    WeightsUpdate,
)
from tuttitrip.profiles.services import access_token_service, profile_service
from tuttitrip.profiles.services.access_token_service import ProfileHasAccountError
from tuttitrip.profiles.services.profile_service import (
    ProfileAccountError,
    ProfileComfortError,
    ProfileForbiddenError,
    ProfileInUseError,
    ProfileNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import no_store, requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.shared.permissions.schemas import AccessTokenCreated, AccessTokenRead
from tuttitrip.shared.permissions.services.token_service import TooManyTokensError
from tuttitrip.trips.api import TripCoHost, TripMember

router = APIRouter(prefix="/trips/{trip_id}/profiles", tags=["profiles"])

PROFILE_IN_USE = "This person has expenses on the trip; delete or reassign them first"
PROFILE_NOT_FOUND = "Profile not found"


@router.get("", dependencies=[requires(Feature.PROFILES_CORE, Access.READ)])
async def list_profiles(
    membership: TripMember, session: SessionDep
) -> list[ProfileRead]:
    """List the people on a trip the caller belongs to.

    Args:
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The trip's profiles.
    """
    return await profile_service.list_profiles(session, membership)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)],
)
async def create_profile(
    data: ProfileCreate, membership: TripCoHost, session: SessionDep
) -> ProfileRead:
    """Add a person; comfort fields not given come from their age.

    Args:
        data: Name, age, optional account link and comfort overrides.
        membership: The caller's co-host (or higher) membership.
        session: Database session.

    Returns:
        The created profile.
    """
    try:
        return await profile_service.create_profile(session, membership, data)
    except ProfileAccountError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProfileComfortError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.put("/weights", dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)])
async def set_weights(
    data: WeightsUpdate, membership: TripCoHost, session: SessionDep
) -> list[ProfileRead]:
    """Set weights by a preset or a list (``max/min`` must stay at most 3).

    Args:
        data: A preset or explicit weights.
        membership: The caller's co-host (or higher) membership.
        session: Database session.

    Returns:
        All profiles of the trip with the new weights.
    """
    try:
        return await profile_service.set_weights(session, membership, data)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except (WeightRatioError, FocusProfileRequiredError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.patch(
    "/{profile_id}", dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)]
)
async def update_profile(
    profile_id: UUID,
    data: ProfileUpdate,
    membership: TripMember,
    session: SessionDep,
) -> ProfileRead:
    """Edit your own profile, or any profile as a co-host or higher.

    Args:
        profile_id: Profile to edit.
        data: Fields to change.
        membership: The caller's membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The updated profile.
    """
    try:
        return await profile_service.update_profile(
            session, membership, profile_id, data
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except ProfileForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ProfileAccountError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProfileComfortError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)],
)
async def delete_profile(
    profile_id: UUID, membership: TripCoHost, session: SessionDep
) -> Response:
    """Remove a person from the trip.

    Args:
        profile_id: Profile to delete.
        membership: The caller's co-host (or higher) membership.
        session: Database session.

    Returns:
        An empty 204 response.
    """
    if await expense_service.profile_in_use(session, membership, profile_id):
        raise HTTPException(status.HTTP_409_CONFLICT, PROFILE_IN_USE)
    try:
        await profile_service.delete_profile(session, membership, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except (ProfileAccountError, ProfileInUseError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{profile_id}/access-tokens",
    dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE), no_store()],
    status_code=status.HTTP_201_CREATED,
)
async def create_vote_token(
    profile_id: UUID,
    data: AccessTokenCreate,
    membership: TripCoHost,
    session: SessionDep,
) -> AccessTokenCreated:
    """Create a voting link token for a person without an account.

    The response is the only time the token is visible. The frontend puts it
    in a URL fragment (`#t=...`) and sends it back as `X-Access-Token`.

    Args:
        profile_id: The person's profile on this trip.
        data: Lifetime of the link.
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The token and its data.
    """
    try:
        return await access_token_service.create_vote_token(
            session, membership, profile_id, data
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
    except ProfileHasAccountError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Profile has an account; it logs in instead"
        ) from exc
    except TooManyTokensError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Too many active tokens (5); revoke one first",
        ) from exc


@router.get(
    "/{profile_id}/access-tokens",
    dependencies=[requires(Feature.PROFILES_CORE, Access.READ)],
)
async def list_access_tokens(
    profile_id: UUID, membership: TripCoHost, session: SessionDep
) -> list[AccessTokenRead]:
    """List a person's link tokens (without the secret), newest first.

    Args:
        profile_id: The person's profile on this trip.
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.

    Returns:
        Token data: id, scope, dates of creation, expiry, last use, revocation.
    """
    try:
        return await access_token_service.list_tokens(session, membership, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc


@router.delete(
    "/{profile_id}/access-tokens/{token_id}",
    dependencies=[requires(Feature.PROFILES_CORE, Access.WRITE)],
)
async def revoke_access_token(
    profile_id: UUID, token_id: UUID, membership: TripCoHost, session: SessionDep
) -> AccessTokenRead:
    """Revoke a link token (idempotent); it answers 404 from then on.

    Args:
        profile_id: The person's profile on this trip.
        token_id: Token id from creation.
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The token's data with `revoked_at`.
    """
    try:
        return await access_token_service.revoke_token(
            session, membership, profile_id, token_id
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND) from exc
