"""Recognise a receipt image by its first bytes (no I/O)."""

MAX_BYTES = 5 * 1024 * 1024
ALLOWED = ("image/jpeg", "image/png", "image/webp")
WEBP_HEADER = 12


def detect_media_type(head: bytes) -> str | None:
    """Tell the image type from the file signature, not from the claimed type.

    Args:
        head: The first bytes of the upload (at least 12).

    Returns:
        ``image/jpeg``, ``image/png`` or ``image/webp``; None for anything else.
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(head) >= WEBP_HEADER and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None
