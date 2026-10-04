"""Rating and veto queries on PostgreSQL."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.feedback.models import PlaceRating, PlaceVeto


async def upsert_rating(session: AsyncSession, rating: PlaceRating) -> PlaceRating:
    """Insert the rating of a person for a place, or replace the existing one.

    Args:
        session: Open session (caller commits).
        rating: A new (transient) rating with trip, profile, place, value,
            reason and author set.

    Returns:
        The stored row.
    """
    stmt = insert(PlaceRating).values(
        trip_id=rating.trip_id,
        profile_id=rating.profile_id,
        place_id=rating.place_id,
        value=rating.value,
        reason_code=rating.reason_code,
        updated_by_sub=rating.updated_by_sub,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_place_ratings_profile_id",
        set_={
            "value": stmt.excluded.value,
            "reason_code": stmt.excluded.reason_code,
            "updated_by_sub": stmt.excluded.updated_by_sub,
            "updated_at": func.now(),
        },
    ).returning(PlaceRating)
    return (
        await session.scalars(stmt, execution_options={"populate_existing": True})
    ).one()


async def select_ratings_by_trip(
    session: AsyncSession, trip_id: UUID
) -> Sequence[PlaceRating]:
    """List every rating of a trip.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Ratings, oldest first.
    """
    result = await session.scalars(
        select(PlaceRating)
        .where(PlaceRating.trip_id == trip_id)
        .order_by(PlaceRating.updated_at, PlaceRating.id)
    )
    return result.all()


async def select_active_vetoes_by_trip(
    session: AsyncSession, trip_id: UUID
) -> Sequence[PlaceVeto]:
    """List the vetoes of a trip that are not revoked.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Active vetoes, oldest first.
    """
    result = await session.scalars(
        select(PlaceVeto)
        .where(PlaceVeto.trip_id == trip_id, PlaceVeto.revoked_at.is_(None))
        .order_by(PlaceVeto.created_at, PlaceVeto.id)
    )
    return result.all()


async def select_veto(
    session: AsyncSession, trip_id: UUID, veto_id: UUID
) -> PlaceVeto | None:
    """One veto of a trip, revoked or not.

    Args:
        session: Open session.
        trip_id: Trip id.
        veto_id: Veto id.

    Returns:
        The veto, or None when missing or on another trip.
    """
    return await session.scalar(
        select(PlaceVeto).where(PlaceVeto.id == veto_id, PlaceVeto.trip_id == trip_id)
    )


async def insert_veto(session: AsyncSession, veto: PlaceVeto) -> PlaceVeto:
    """Insert a veto and flush.

    Args:
        session: Open session (caller commits).
        veto: The new veto.

    Returns:
        The same veto.
    """
    session.add(veto)
    await session.flush()
    return veto


async def select_ratings_of_profile(
    session: AsyncSession, trip_id: UUID, profile_id: UUID
) -> Sequence[PlaceRating]:
    """List one person's ratings on a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        profile_id: Whose ratings.

    Returns:
        Ratings, oldest first.
    """
    result = await session.scalars(
        select(PlaceRating)
        .where(PlaceRating.trip_id == trip_id, PlaceRating.profile_id == profile_id)
        .order_by(PlaceRating.updated_at, PlaceRating.id)
    )
    return result.all()


async def select_active_vetoes_of_profile(
    session: AsyncSession, trip_id: UUID, profile_id: UUID
) -> Sequence[PlaceVeto]:
    """List one person's vetoes in force on a trip.

    Args:
        session: Open session.
        trip_id: Trip id.
        profile_id: Whose vetoes.

    Returns:
        Active vetoes, oldest first.
    """
    result = await session.scalars(
        select(PlaceVeto)
        .where(
            PlaceVeto.trip_id == trip_id,
            PlaceVeto.profile_id == profile_id,
            PlaceVeto.revoked_at.is_(None),
        )
        .order_by(PlaceVeto.created_at, PlaceVeto.id)
    )
    return result.all()
