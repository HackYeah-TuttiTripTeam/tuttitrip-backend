"""``/admin/planning/parameters``: parameters and weights of the algorithm."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from tuttitrip.planning.parameters.schemas import (
    ParametersCreate,
    ParametersQuery,
    ParametersRead,
)
from tuttitrip.planning.parameters.services import parameters_service
from tuttitrip.shared.auth.api import CurrentUser
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/admin/planning/parameters", tags=["admin"])


@router.get(
    "",
    summary="Parameters in force",
    description=(
        "The newest version of the algorithm parameters (section 6 of "
        "`docs/algorytm.md`); version 0 is the built-in default. `alpha` is the "
        "default slider of new trips."
    ),
    dependencies=[requires(Feature.ADMIN_PLANNING_WEIGHTS, Access.READ)],
)
async def get_parameters(session: SessionDep) -> ParametersRead:
    """Read the parameters in force.

    Args:
        session: Database session.

    Returns:
        The newest version.
    """
    return await parameters_service.read_current(session)


@router.get(
    "/versions",
    summary="History of parameter versions",
    description="Paged, newest first by default.",
    dependencies=[requires(Feature.ADMIN_PLANNING_WEIGHTS, Access.READ)],
)
async def list_versions(
    session: SessionDep, query: Annotated[ParametersQuery, Query()]
) -> Page[ParametersRead]:
    """One page of versions.

    Args:
        session: Database session.
        query: Page and direction.

    Returns:
        The page.
    """
    return await parameters_service.list_versions(session, query)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Store a new version of the parameters",
    description=(
        "The whole set (omitted fields take the default), checked against the "
        "ranges of the specification: a value outside gets 422. New plans use "
        "the new version and record its number; stored plans are not recomputed."
    ),
    dependencies=[requires(Feature.ADMIN_PLANNING_WEIGHTS, Access.WRITE)],
)
async def create_parameters(
    session: SessionDep, user: CurrentUser, data: ParametersCreate
) -> ParametersRead:
    """Store a new version.

    Args:
        session: Database session.
        user: The administrator.
        data: Parameters and a note.

    Returns:
        The stored version.
    """
    return await parameters_service.create_version(session, user.sub, data)
