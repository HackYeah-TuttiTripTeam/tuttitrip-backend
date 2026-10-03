"""Store and read pasted plan and offer texts (also used by accommodation)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.linter import db
from tuttitrip.planning.linter.schemas import DocumentCreate, DocumentRead
from tuttitrip.trips.schemas import TripMembership


class DocumentNotFoundError(Exception):
    """No pasted document with this id on the trip."""


async def create_document(
    session: AsyncSession, membership: TripMembership, data: DocumentCreate
) -> DocumentRead:
    """Save a pasted text for the trip the caller was checked for.

    Args:
        session: Open session.
        membership: Proof from ``TripAccess``.
        data: Kind and text (at most 20 000 characters, checked by the schema).

    Returns:
        The stored document.
    """
    row = await db.insert_document(
        session,
        trip_id=membership.trip_id,
        kind=data.kind,
        text=data.text,
        created_by=membership.sub,
    )
    await session.commit()
    return DocumentRead.model_validate(row)


async def get_document(
    session: AsyncSession, trip_id: UUID, document_id: UUID
) -> DocumentRead:
    """Read a pasted text of a trip.

    Args:
        session: Open session.
        trip_id: Trip the document must belong to.
        document_id: Document id.

    Returns:
        The document.

    Raises:
        DocumentNotFoundError: Unknown id, or it belongs to another trip.
    """
    row = await db.select_document(session, trip_id, document_id)
    if row is None:
        raise DocumentNotFoundError(str(document_id))
    return DocumentRead.model_validate(row)
