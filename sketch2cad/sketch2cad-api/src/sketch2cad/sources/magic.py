"""Media type detection from the leading bytes of a file, never from its name (V-01)."""

from sketch2cad.db.models import MediaType

SNIFF_BYTES = 8
"""Number of leading bytes needed to recognise every accepted signature."""

_SIGNATURES: tuple[tuple[bytes, MediaType], ...] = (
    (b"\xff\xd8\xff", MediaType.JPEG),
    (b"\x89PNG\r\n\x1a\n", MediaType.PNG),
    (b"II*\x00", MediaType.TIFF),
    (b"MM\x00*", MediaType.TIFF),
    (b"II+\x00", MediaType.TIFF),  # BigTIFF, little-endian
    (b"MM\x00+", MediaType.TIFF),  # BigTIFF, big-endian
    (b"%PDF-", MediaType.PDF),
)


def sniff_media_type(head: bytes) -> MediaType | None:
    """Return the media type whose signature starts ``head``, or ``None`` if none does."""
    for signature, media_type in _SIGNATURES:
        if head.startswith(signature):
            return media_type
    return None
