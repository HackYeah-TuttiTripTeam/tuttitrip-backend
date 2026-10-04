"""What an uploaded photo may be, and who may delete it.

The browser shrinks the picture (which also strips EXIF), so the server only
guards the limits: the real type is read from the file's first bytes, never
taken from the client's header or file name (the name is not stored at all).
"""

from tuttitrip.trips.schemas import TripRole

JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
ALLOWED_TYPES = (JPEG, PNG, WEBP)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_RIFF_HEADER = 12


def sniff_type(data: bytes) -> str | None:
    """Detect a supported image type from the file's magic bytes.

    Args:
        data: The uploaded bytes.

    Returns:
        ``image/jpeg``, ``image/png``, ``image/webp``, or None for anything else.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith(_PNG_MAGIC):
        return PNG
    if len(data) >= _RIFF_HEADER and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    return None


def problem(data: bytes, limit: int, what: str) -> str | None:
    """Describe why an upload is not acceptable.

    Args:
        data: The uploaded bytes (at most ``limit + 1`` of them were read).
        limit: Largest accepted size in bytes.
        what: Name used in the message (``image`` or ``thumbnail``).

    Returns:
        A message, or None when the upload is fine.
    """
    if not data:
        return f"The {what} is empty"
    if len(data) > limit:
        return f"The {what} is larger than {limit} bytes"
    if sniff_type(data) is None:
        return f"The {what} must be a JPEG, PNG or WebP image"
    return None


def can_delete(role: TripRole, caller_sub: str, author_sub: str) -> bool:
    """Whether the caller may delete a photo: its author, or the host.

    Args:
        role: The caller's role on the trip.
        caller_sub: Auth0 subject of the caller.
        author_sub: Auth0 subject of the photo's author.

    Returns:
        True when the deletion is allowed.
    """
    return caller_sub == author_sub or role is TripRole.HOST
