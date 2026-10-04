"""Voting endpoints for the host: links for people without an account and results.

The voting itself (rating and veto through a link) is a separate slice. These
routes need an account with the `trips.vote_links` permission and co-host rights
on the trip. The token appears once, in the response that creates a link.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tuttitrip.profiles.services.profile_service import ProfileNotFoundError
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import no_store, requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.shared.permissions.schemas import AccessTokenQuery
from tuttitrip.shared.permissions.services.token_service import TooManyTokensError
from tuttitrip.trips.api import TripCoHost
from tuttitrip.voting.schemas import (
    PlaceVoteSummary,
    VoteLinkCreate,
    VoteLinkCreated,
    VoteLinkRead,
    VoteSummaryQuery,
)
from tuttitrip.voting.services import vote_link_service, vote_summary_service
from tuttitrip.voting.services.vote_link_service import (
    VoteLinkNotFoundError,
    VoteLinkProfileHasAccountError,
)

router = APIRouter(prefix="/trips/{trip_id}", tags=["voting"])


@router.post(
    "/vote-links",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "`profile_id` is not a profile of this trip."},
        409: {
            "description": (
                "The person has an account (they log in to vote), or has too "
                "many working tokens."
            )
        },
    },
    dependencies=[requires(Feature.TRIPS_VOTE_LINKS, Access.WRITE), no_store()],
)
async def create_vote_link(
    data: VoteLinkCreate, membership: TripCoHost, session: SessionDep
) -> VoteLinkCreated:
    """Create the voting link of a person without an account (co-host or host).

    The person has one working link: a new one revokes the previous. The token
    is visible only in this response; build the QR code from `url`.

    Args:
        data: Person and lifetime.
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The link and its token.
    """
    try:
        return await vote_link_service.create_link(session, membership, data)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found") from exc
    except TooManyTokensError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Too many working tokens for this person"
        ) from exc
    except VoteLinkProfileHasAccountError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This person has an account and logs in"
        ) from exc


@router.get(
    "/vote-links",
    dependencies=[requires(Feature.TRIPS_VOTE_LINKS, Access.READ)],
)
async def list_vote_links(
    membership: TripCoHost,
    session: SessionDep,
    query: Annotated[AccessTokenQuery, Query()],
) -> Page[VoteLinkRead]:
    """List the trip's voting links without secrets, newest first by default.

    Args:
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.
        query: Page, sort and filters (person, state).

    Returns:
        One page of links.
    """
    return await vote_link_service.list_links(session, membership, query)


@router.delete(
    "/vote-links/{link_id}",
    responses={404: {"description": "No such voting link on this trip."}},
    dependencies=[requires(Feature.TRIPS_VOTE_LINKS, Access.WRITE)],
)
async def revoke_vote_link(
    link_id: UUID, membership: TripCoHost, session: SessionDep
) -> VoteLinkRead:
    """Revoke a voting link (idempotent); the votes already cast stay.

    Args:
        link_id: Link id from creation or the list.
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.

    Returns:
        The link with `revoked_at`.
    """
    try:
        return await vote_link_service.revoke_link(session, membership, link_id)
    except VoteLinkNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found") from exc


@router.get(
    "/vote-summary",
    dependencies=[requires(Feature.TRIPS_VOTE_LINKS, Access.READ)],
)
async def read_vote_summary(
    membership: TripCoHost,
    session: SessionDep,
    query: Annotated[VoteSummaryQuery, Query()],
) -> Page[PlaceVoteSummary]:
    """Per place: who is for, who is against and why, and who vetoed.

    Each entry says where the vote came from: `app` (the person themselves),
    `link` (a voting link) or `host` (a co-host acting on their behalf). Only
    places somebody voted on or vetoed are listed. Counts are unweighted.

    Args:
        membership: The caller's (co-host) membership of ``{trip_id}``.
        session: Database session.
        query: Page, sort and filters (veto, source).

    Returns:
        One page of places, most vetoed first by default.
    """
    return await vote_summary_service.get_summary(session, membership, query)
