"""Queries on the demo domain's tables."""

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.demo.models import SampleTripGrant


async def has_sample_grant(session: AsyncSession, sub: str) -> bool:
    """Whether the account already got its sample trip.

    Args:
        session: Open session.
        sub: Auth0 subject.

    Returns:
        True when the mark exists.
    """
    found = await session.scalar(
        select(func.count())
        .select_from(SampleTripGrant)
        .where(SampleTripGrant.user_sub == sub)
    )
    return bool(found)


async def lock_sample_grant(session: AsyncSession, sub: str) -> None:
    """Serialize the creations for one account until the transaction ends.

    Args:
        session: Open session (the lock is released at commit or rollback).
        sub: Auth0 subject.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:sub, 0))"), {"sub": sub}
    )


async def add_sample_grant(session: AsyncSession, sub: str) -> None:
    """Mark the account (idempotent), without committing.

    Args:
        session: Open session (caller commits).
        sub: Auth0 subject.
    """
    await session.execute(
        insert(SampleTripGrant)
        .values(user_sub=sub)
        .on_conflict_do_nothing(index_elements=["user_sub"])
    )


async def delete_sample_grant(session: AsyncSession, sub: str) -> int:
    """Remove the account's mark, without committing.

    Args:
        session: Open session (caller commits).
        sub: Auth0 subject.

    Returns:
        How many marks were removed (0 or 1).
    """
    result = await session.execute(
        delete(SampleTripGrant)
        .where(SampleTripGrant.user_sub == sub)
        .returning(SampleTripGrant.user_sub)
    )
    return len(result.all())
