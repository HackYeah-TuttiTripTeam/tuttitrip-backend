"""Embedding queries on PostgreSQL."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.search.models import Embedding


async def select_nearest(
    session: AsyncSession, vector: list[float], *, limit: int
) -> Sequence[Embedding]:
    """Nearest embeddings by cosine distance.

    Args:
        session: Open session.
        vector: Query embedding (same model as the stored ones).
        limit: Maximum number of rows.

    Returns:
        The closest embeddings first.
    """
    result = await session.scalars(
        select(Embedding)
        .order_by(Embedding.embedding.cosine_distance(vector))
        .limit(limit)
    )
    return result.all()
