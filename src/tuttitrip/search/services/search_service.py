"""Vector search over embeddings written by the worker."""

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.search import db
from tuttitrip.search.schemas import EmbeddingHit


async def nearest(
    session: AsyncSession, vector: list[float], limit: int = 10
) -> list[EmbeddingHit]:
    """Find the closest stored content.

    Args:
        session: Open session.
        vector: Query embedding.
        limit: Maximum number of hits.

    Returns:
        Hits, closest first.
    """
    rows = await db.select_nearest(session, vector, limit=limit)
    return [EmbeddingHit.model_validate(row) for row in rows]
