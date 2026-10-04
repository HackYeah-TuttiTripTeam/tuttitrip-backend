"""What an uploaded photo may be, and who may delete it.

The browser re-encodes the picture (which drops EXIF), so the server guards
the limits and refuses what still carries location metadata. The real type is
read from the file's first bytes, never from the client's header or file name
(the name is not stored at all). Nothing is decoded: the metadata check only
walks the container's segments or chunks.
"""

from tuttitrip.trips.schemas import TripRole

JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
ALLOWED_TYPES = (JPEG, PNG, WEBP)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_RIFF_HEADER = 12
_EXIF_JPEG = b"Exif\x00"
_XMP_JPEG = b"http://ns.adobe.com/xap/1.0/"
_XMP_PNG = b"XML:com.adobe.xmp"
_JPEG_PREFIX = 0xFF
_JPEG_SOS = 0xDA
_JPEG_EOI = 0xD9
_JPEG_APP1 = 0xE1
_JPEG_STANDALONE = frozenset({0x01, 0xD8, *range(0xD0, 0xD8)})


class UploadRejectedError(Exception):
    """An uploaded file breaks a rule; the message says which."""


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


def _jpeg_has_metadata(data: bytes) -> bool:
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != _JPEG_PREFIX:
            return False
        marker = data[pos + 1]
        if marker == _JPEG_PREFIX:
            pos += 1
        elif marker in _JPEG_STANDALONE:
            pos += 2
        elif marker in {_JPEG_SOS, _JPEG_EOI}:
            return False
        else:
            length = int.from_bytes(data[pos + 2 : pos + 4], "big")
            segment = data[pos + 4 : pos + 2 + length]
            if marker == _JPEG_APP1 and segment.startswith((_EXIF_JPEG, _XMP_JPEG)):
                return True
            pos += 2 + max(length, 2)
    return False


def _png_has_metadata(data: bytes) -> bool:
    pos = len(_PNG_MAGIC)
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        if kind == b"eXIf" or (
            kind == b"iTXt" and data[pos + 8 :].startswith(_XMP_PNG)
        ):
            return True
        pos += 12 + length
    return False


def _webp_has_metadata(data: bytes) -> bool:
    pos = _RIFF_HEADER
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        if kind in {b"EXIF", b"XMP "}:
            return True
        pos += 8 + size + (size & 1)
    return False


def has_metadata(data: bytes, content_type: str) -> bool:
    """Whether the image carries EXIF or XMP (where GPS coordinates live).

    Args:
        data: The uploaded bytes.
        content_type: Their sniffed type.

    Returns:
        True when an EXIF or XMP block is present.
    """
    if content_type == JPEG:
        return _jpeg_has_metadata(data)
    if content_type == PNG:
        return _png_has_metadata(data)
    return _webp_has_metadata(data)


def accept(data: bytes, limit: int, what: str) -> str:
    """Check an upload and tell its real type.

    Args:
        data: The uploaded bytes (at most ``limit + 1`` of them were read).
        limit: Largest accepted size in bytes.
        what: Name used in messages (``image`` or ``thumbnail``).

    Returns:
        The sniffed content type.

    Raises:
        UploadRejectedError: Empty, too large, not JPEG/PNG/WebP, or it still
            carries EXIF/XMP metadata.
    """
    if not data:
        msg = f"The {what} is empty"
        raise UploadRejectedError(msg)
    if len(data) > limit:
        msg = f"The {what} is larger than {limit} bytes"
        raise UploadRejectedError(msg)
    content_type = sniff_type(data)
    if content_type is None:
        msg = f"The {what} must be a JPEG, PNG or WebP image"
        raise UploadRejectedError(msg)
    if has_metadata(data, content_type):
        msg = f"The {what} carries EXIF/XMP metadata (location); re-encode it first"
        raise UploadRejectedError(msg)
    return content_type


def can_delete(role: TripRole, caller_sub: str, author_sub: str | None) -> bool:
    """Whether the caller may delete a photo: its author, or the host.

    Args:
        role: The caller's role on the trip.
        caller_sub: Auth0 subject of the caller.
        author_sub: Auth0 subject of the photo's author; None once they left.

    Returns:
        True when the deletion is allowed.
    """
    return caller_sub == author_sub or role is TripRole.HOST
