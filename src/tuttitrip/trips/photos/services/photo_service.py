"""Photos: members upload, list, view and delete trip pictures.

Files are ``bytea`` in Postgres. The service checks size and real type again
(the API only reads the bytes), enforces the per-trip limit and decides who
deletes. Everything is scoped by the checked membership, so a photo id from
another trip is simply not found.
"""

import base64
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.trips.photos import db
from tuttitrip.trips.photos.logic import rules
from tuttitrip.trips.photos.models import TripPhoto
from tuttitrip.trips.photos.schemas import PhotoQuery, PhotoRead
from tuttitrip.trips.schemas import TripMembership


class PhotoRejectedError(Exception):
    """The upload breaks a limit (size, type or photos per trip)."""


class PhotoNotFoundError(Exception):
    """No such photo on this trip."""


class PhotoForbiddenError(Exception):
    """Only the author or the host may delete a photo."""


def _read(photo: TripPhoto, names: dict[str, str], caller_sub: str) -> PhotoRead:
    encoded = base64.b64encode(photo.thumbnail).decode("ascii")
    return PhotoRead(
        id=photo.id,
        author_name=names.get(photo.author_sub),
        is_mine=photo.author_sub == caller_sub,
        content_type=photo.content_type,
        size_bytes=photo.size_bytes,
        created_at=photo.created_at,
        thumbnail=f"data:{photo.thumbnail_type};base64,{encoded}",
    )


async def _names(session: AsyncSession, membership: TripMembership) -> dict[str, str]:
    profiles = await profile_service.list_profiles(session, membership)
    return {p.user_sub: p.display_name for p in profiles if p.user_sub is not None}


async def upload(
    session: AsyncSession, membership: TripMembership, image: bytes, thumbnail: bytes
) -> PhotoRead:
    """Store a photo and commit.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        image: The resized picture.
        thumbnail: Its small preview.

    Returns:
        The stored photo.

    Raises:
        PhotoRejectedError: Too large, empty, not JPEG/PNG/WebP, or the trip is full.
    """
    limits = get_settings().photos
    for data, limit, what in (
        (image, limits.max_image_bytes, "image"),
        (thumbnail, limits.max_thumbnail_bytes, "thumbnail"),
    ):
        if (message := rules.problem(data, limit, what)) is not None:
            raise PhotoRejectedError(message)
    if await db.count_photos(session, membership.trip_id) >= limits.max_per_trip:
        msg = f"A trip holds at most {limits.max_per_trip} photos"
        raise PhotoRejectedError(msg)
    content_type = rules.sniff_type(image)
    thumbnail_type = rules.sniff_type(thumbnail)
    if content_type is None or thumbnail_type is None:  # unreachable: checked above
        msg = "Unsupported image type"
        raise PhotoRejectedError(msg)
    photo = await db.insert_photo(
        session,
        TripPhoto(
            trip_id=membership.trip_id,
            author_sub=membership.sub,
            content_type=content_type,
            size_bytes=len(image),
            thumbnail_type=thumbnail_type,
            thumbnail=thumbnail,
            image=image,
        ),
    )
    await session.commit()
    return _read(photo, await _names(session, membership), membership.sub)


async def list_photos(
    session: AsyncSession, membership: TripMembership, query: PhotoQuery
) -> Page[PhotoRead]:
    """One page of the trip's photos with inlined thumbnails.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        query: Page, sort, direction and filters.

    Returns:
        The page.
    """
    page = await db.select_photos(session, membership.trip_id, membership.sub, query)
    names = await _names(session, membership)
    return Page[PhotoRead](
        items=[_read(p, names, membership.sub) for p in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )


async def get_image(
    session: AsyncSession, membership: TripMembership, photo_id: UUID
) -> tuple[bytes, str]:
    """The full image of a photo.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        photo_id: Photo id.

    Returns:
        The bytes and their content type.

    Raises:
        PhotoNotFoundError: No such photo on this trip.
    """
    photo = await db.select_photo(
        session, membership.trip_id, photo_id, with_image=True
    )
    if photo is None:
        raise PhotoNotFoundError(str(photo_id))
    return photo.image, photo.content_type


async def delete(
    session: AsyncSession, membership: TripMembership, photo_id: UUID
) -> None:
    """Delete a photo and commit.

    Args:
        session: Open session.
        membership: The caller's checked membership (any role).
        photo_id: Photo id.

    Raises:
        PhotoNotFoundError: No such photo on this trip.
        PhotoForbiddenError: The caller is neither the author nor the host.
    """
    photo = await db.select_photo(
        session, membership.trip_id, photo_id, with_image=False
    )
    if photo is None:
        raise PhotoNotFoundError(str(photo_id))
    if not rules.can_delete(membership.role, membership.sub, photo.author_sub):
        msg = "Only the author or the host can delete a photo"
        raise PhotoForbiddenError(msg)
    await db.delete_photo(session, membership.trip_id, photo_id)
    await session.commit()
