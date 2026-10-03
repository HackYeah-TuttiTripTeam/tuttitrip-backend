"""Pasted document queries on PostgreSQL."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.linter.models import PastedDocument


async def insert_document(
    session: AsyncSession, *, trip_id: UUID, kind: str, text: str, created_by: str
) -> PastedDocument:
    """Insert a pasted text and flush to get server defaults.

    Args:
        session: Open session (caller commits).
        trip_id: Trip the text belongs to.
        kind: ``plan`` or ``offer``.
        text: The pasted text.
        created_by: Auth0 subject of the author.

    Returns:
        The persisted row.
    """
    document = PastedDocument(
        trip_id=trip_id, kind=kind, text=text, created_by=created_by
    )
    session.add(document)
    await session.flush()
    await session.refresh(document)
    return document
