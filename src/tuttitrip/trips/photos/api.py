"""Photo endpoints: upload, list, view and delete trip pictures (members only)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status

from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.api import SessionDep
from tuttitrip.shared.pagination.schemas import Page
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.api import TripMember
from tuttitrip.trips.photos.schemas import PhotoQuery, PhotoRead
from tuttitrip.trips.photos.services import photo_service
from tuttitrip.trips.photos.services.photo_service import (
    PhotoForbiddenError,
    PhotoNotFoundError,
    PhotoRejectedError,
)

router = APIRouter(prefix="/trips/{trip_id}/photos", tags=["photos"])

PHOTO_NOT_FOUND = "Photo not found"
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Not a member of the trip, or no such photo on it."}
}
IMAGE_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The image bytes.",
        "content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}},
    },
    **NOT_FOUND,
}


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes, so an oversize file is seen but not held.

    Args:
        file: The uploaded part.
        limit: Largest accepted size in bytes.

    Returns:
        Up to ``limit + 1`` bytes.
    """
    return await file.read(limit + 1)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        **NOT_FOUND,
        422: {"description": "Empty, too large, not JPEG/PNG/WebP, or trip is full."},
    },
    dependencies=[requires(Feature.TRIPS_CORE, Access.WRITE)],
)
async def upload_photo(
    membership: TripMember,
    session: SessionDep,
    image: Annotated[UploadFile, File(description="The resized picture.")],
    thumbnail: Annotated[UploadFile, File(description="Its small preview.")],
) -> PhotoRead:
    """Add a photo (`multipart/form-data`, parts `image` and `thumbnail`).

    The browser resizes the picture (dropping EXIF) and makes the thumbnail.
    JPEG, PNG and WebP only (the type is read from the bytes, not the header);
    limits come from the `photos` settings: 2 MB image, 60 KB thumbnail and 200
    photos per trip by default. Anything over a limit answers 422.

    Args:
        membership: The caller's membership of `{trip_id}`.
        session: Database session.
        image: The picture.
        thumbnail: The preview.

    Returns:
        The stored photo.
    """
    limits = get_settings().photos
    try:
        return await photo_service.upload(
            session,
            membership,
            await _read_limited(image, limits.max_image_bytes),
            await _read_limited(thumbnail, limits.max_thumbnail_bytes),
        )
    except PhotoRejectedError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get(
    "",
    summary="Photos of the trip",
    responses=NOT_FOUND,
    dependencies=[requires(Feature.TRIPS_CORE, Access.READ)],
)
async def list_photos(
    membership: TripMember,
    session: SessionDep,
    query: Annotated[PhotoQuery, Query()],
) -> Page[PhotoRead]:
    """List the trip's photos with inlined thumbnails, newest first by default.

    Args:
        membership: The caller's membership of `{trip_id}`.
        session: Database session.
        query: Page, sort and filters.

    Returns:
        One page of photos.
    """
    return await photo_service.list_photos(session, membership, query)


@router.get(
    "/{photo_id}/image",
    response_class=Response,
    responses=IMAGE_RESPONSE,
    dependencies=[requires(Feature.TRIPS_CORE, Access.READ)],
)
async def get_photo_image(
    photo_id: UUID, membership: TripMember, session: SessionDep
) -> Response:
    """Download the full image (members only; needs the bearer token).

    Args:
        photo_id: Photo id from the list.
        membership: The caller's membership of `{trip_id}`.
        session: Database session.

    Returns:
        The image with its content type; not cacheable by shared caches.
    """
    try:
        data, content_type = await photo_service.get_image(
            session, membership, photo_id
        )
    except PhotoNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PHOTO_NOT_FOUND) from exc
    return Response(
        data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **NOT_FOUND,
        403: {"description": "Neither the author nor the host."},
    },
    dependencies=[requires(Feature.TRIPS_CORE, Access.WRITE)],
)
async def delete_photo(
    photo_id: UUID, membership: TripMember, session: SessionDep
) -> None:
    """Delete a photo (its author or the host).

    Args:
        photo_id: Photo id from the list.
        membership: The caller's membership of `{trip_id}`.
        session: Database session.
    """
    try:
        await photo_service.delete(session, membership, photo_id)
    except PhotoNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PHOTO_NOT_FOUND) from exc
    except PhotoForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
