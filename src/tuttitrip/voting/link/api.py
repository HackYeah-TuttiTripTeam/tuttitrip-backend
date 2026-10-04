"""Voting through a link: no account, the trip and the person come from the token.

Every route carries ``token_access(TokenScope.VOTE)`` and no ``{trip_id}``. A
dead link (unknown, expired, revoked, or its profile has an account now) and an
unknown place both answer 404 with ``Cache-Control: no-store``.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from tuttitrip.profiles.feedback.schemas import RatingUpdate
from tuttitrip.profiles.feedback.services.feedback_service import (
    FeedbackPlaceNotFoundError,
    VetoNotFoundError,
)
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.permissions.api import (
    NO_STORE_HEADERS,
    TOKEN_NOT_FOUND,
    TokenAccessDep,
    token_access,
)
from tuttitrip.shared.permissions.schemas import TokenScope
from tuttitrip.voting.link.schemas import VotePlace, VoteSession, VoteVetoCreate
from tuttitrip.voting.link.services import vote_session_service
from tuttitrip.voting.link.services.vote_session_service import VoteLinkDeadError

router = APIRouter(prefix="/vote", tags=["vote"])

_DEAD_LINK: dict[int | str, dict[str, Any]] = {
    404: {
        "description": (
            "Unknown, expired or revoked token, or the person has an account now "
            "(they log in to vote). The answer is the same for all of them."
        )
    },
    401: {"description": "No `X-Access-Token` header."},
}


def _not_found(exc: Exception) -> HTTPException:
    detail = (
        "Place not found"
        if isinstance(exc, FeedbackPlaceNotFoundError)
        else TOKEN_NOT_FOUND
    )
    return HTTPException(status.HTTP_404_NOT_FOUND, detail, headers=NO_STORE_HEADERS)


@router.get(
    "/session",
    responses=_DEAD_LINK,
    dependencies=[token_access(TokenScope.VOTE)],
)
async def read_vote_session(access: TokenAccessDep, session: SessionDep) -> VoteSession:
    """The places of the current plan with this person's own answers.

    Nothing about other people: no preferences, ratings or vetoes of theirs.

    Args:
        access: The checked token (trip and person come from it).
        session: Database session.

    Returns:
        Trip name, the person's name and the places.
    """
    try:
        return await vote_session_service.read_session(session, access)
    except VoteLinkDeadError as exc:
        raise _not_found(exc) from exc


@router.put(
    "/ratings/{place_id}",
    responses={**_DEAD_LINK, 422: {"description": "`dont_want` needs a reason."}},
    dependencies=[token_access(TokenScope.VOTE)],
)
async def rate_place_by_link(
    place_id: UUID, data: RatingUpdate, access: TokenAccessDep, session: SessionDep
) -> VotePlace:
    """Rate a place: want, neutral, or do not want with a reason.

    Args:
        place_id: Catalog place.
        data: Value and, for `dont_want`, the reason code.
        access: The checked token.
        session: Database session.

    Returns:
        The place with the stored answer.
    """
    try:
        return await vote_session_service.rate(session, access, place_id, data)
    except (VoteLinkDeadError, FeedbackPlaceNotFoundError) as exc:
        raise _not_found(exc) from exc


@router.post(
    "/vetoes",
    responses=_DEAD_LINK,
    dependencies=[token_access(TokenScope.VOTE)],
)
async def veto_place_by_link(
    data: VoteVetoCreate, access: TokenAccessDep, session: SessionDep
) -> VotePlace:
    """Veto a place and recompute the plan at once.

    Repeating the veto of the same place keeps one. The new plan version is
    stored in this request; the host sees it as the latest plan.

    Args:
        data: The place.
        access: The checked token.
        session: Database session.

    Returns:
        The place with `veto_id` set.
    """
    try:
        return await vote_session_service.veto(session, access, data.place_id)
    except (VoteLinkDeadError, FeedbackPlaceNotFoundError) as exc:
        raise _not_found(exc) from exc


@router.delete(
    "/vetoes/{veto_id}",
    responses=_DEAD_LINK,
    dependencies=[token_access(TokenScope.VOTE)],
)
async def withdraw_veto_by_link(
    veto_id: UUID, access: TokenAccessDep, session: SessionDep
) -> VotePlace:
    """Withdraw this person's own veto and recompute the plan.

    Args:
        veto_id: `veto_id` from the session.
        access: The checked token.
        session: Database session.

    Returns:
        The place with `veto_id` cleared.
    """
    try:
        return await vote_session_service.revoke_veto(session, access, veto_id)
    except (VoteLinkDeadError, VetoNotFoundError) as exc:
        raise _not_found(exc) from exc
