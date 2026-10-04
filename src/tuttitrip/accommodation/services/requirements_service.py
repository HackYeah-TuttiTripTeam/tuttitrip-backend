"""Check an offer against the contract; read and replace a trip's requirements."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.accommodation import db
from tuttitrip.accommodation.logic.contract import evaluate
from tuttitrip.accommodation.models import AccommodationRequirement
from tuttitrip.accommodation.schemas import (
    OfferFeatures,
    Requirement,
    RequirementCheck,
    RequirementItem,
    RequirementsRead,
    RequirementsWrite,
)
from tuttitrip.trips.schemas import OUTING, TripMembership
from tuttitrip.trips.services import trip_service


class RequirementsInvalidError(Exception):
    """The requirements do not fit the trip (an outing has no lodging)."""


def check_offer(
    requirements: Iterable[Requirement], offer: OfferFeatures
) -> list[RequirementCheck]:
    """Evaluate every requirement.

    Args:
        requirements: The contract.
        offer: Known offer features.

    Returns:
        One check per requirement.
    """
    return [evaluate(requirement, offer) for requirement in requirements]


def _ordered(items: list[RequirementItem]) -> list[RequirementItem]:
    return sorted(items, key=lambda item: (item.kind, item.key))


async def get_requirements(session: AsyncSession, trip_id: UUID) -> RequirementsRead:
    """Read the stored requirements of a trip.

    Args:
        session: Open session.
        trip_id: Trip id (the caller was already checked).

    Returns:
        The requirements with their version.
    """
    rows = await db.select_requirements(session, trip_id)
    return RequirementsRead(
        requirements=[RequirementItem.model_validate(row) for row in rows],
        version=await db.select_version(session, trip_id),
    )


async def replace_requirements(
    session: AsyncSession, membership: TripMembership, data: RequirementsWrite
) -> RequirementsRead:
    """Replace the trip's requirements and commit; the version moves on a change.

    Interview tools use this same function, there is no second table.

    Args:
        session: Open session.
        membership: Proof from ``TripAccess`` (co-host or host).
        data: The whole new set.

    Returns:
        The stored requirements with the new version.

    Raises:
        RequirementsInvalidError: The trip is an outing (no overnight stays).
    """
    trip = await trip_service.get_trip(session, membership)
    if trip.kind == OUTING and data.requirements:
        msg = "An outing has no overnight stays, so it takes no lodging requirements"
        raise RequirementsInvalidError(msg)
    current = await get_requirements(session, membership.trip_id)
    changed = _ordered(current.requirements) != _ordered(data.requirements)
    rows = [
        AccommodationRequirement(trip_id=membership.trip_id, **item.model_dump())
        for item in data.requirements
    ]
    if changed:
        await db.replace_requirements(session, membership.trip_id, rows)
        await db.bump_version(session, membership.trip_id)
    version = await db.select_version(session, membership.trip_id)
    await session.commit()
    return RequirementsRead(requirements=_ordered(data.requirements), version=version)
