"""Derived JPEG previews of stored sources; the original is never modified.

Previews keep the stored pixel orientation (no EXIF rotation), so they map 1:1 onto the
``width_px`` and ``height_px`` recorded at ingestion, and carry no EXIF or other metadata.
PDFs are previewed by rendering page 1 with pypdfium2 at the requested size.
"""

import io
import logging
from typing import IO, Any, cast

import pypdfium2  # pyright: ignore[reportMissingTypeStubs]
from PIL import Image

from sketch2cad.db.models import MediaType

logger = logging.getLogger(__name__)

PREVIEW_MEDIA_TYPE = "image/jpeg"
MIN_PREVIEW_SIDE = 256
MAX_PREVIEW_SIDE = 4096
DEFAULT_PREVIEW_SIDE = 2048
JPEG_QUALITY = 85

_HIGH_DEPTH_MODES = frozenset({"I", "I;16", "I;16B", "I;16L", "I;16N", "F"})
_BACKGROUND = (255, 255, 255)


class PreviewError(ValueError):
    """The stored source could not be decoded into a preview."""


def _to_jpeg_mode(image: Image.Image) -> Image.Image:
    """Grayscale or RGB copy of ``image``; transparency is flattened onto white."""
    if image.mode in ("L", "RGB"):
        return image
    if image.mode in _HIGH_DEPTH_MODES:
        # Scale 16-bit (or wider) samples to 8 bits instead of clipping them.
        return image.convert("I").point(lambda value: value * (1 / 256)).convert("L")
    if image.mode in ("1", "LA") or (image.mode == "P" and _is_gray_palette(image)):
        image = image.convert("LA")
        background = Image.new("L", image.size, 255)
        background.paste(image, mask=image.getchannel("A"))
        return background
    rgba = image.convert("RGBA")
    background = Image.new("RGB", rgba.size, _BACKGROUND)
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def _is_gray_palette(image: Image.Image) -> bool:
    palette = image.getpalette() or []
    triples = zip(palette[0::3], palette[1::3], palette[2::3], strict=False)
    return all(red == green == blue for red, green, blue in triples)


def _open_image(stream: IO[bytes]) -> Image.Image:
    try:
        with Image.open(stream) as image:
            image.load()
            return image.copy()
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise PreviewError("unreadable image") from None


def _render_pdf(stream: IO[bytes], max_side: int) -> Image.Image:
    try:
        document = pypdfium2.PdfDocument(stream)
    except pypdfium2.PdfiumError:
        raise PreviewError("unreadable PDF") from None
    try:
        page = document[0]
        try:
            width, height = page.get_size()
            scale = max_side / max(width, height, 1.0)
            bitmap = cast(Any, page.render(scale=scale))  # pyright: ignore[reportUnknownMemberType]
            try:
                # The PIL image may share the bitmap's buffer; copy it before closing.
                image = cast(Image.Image, bitmap.to_pil().copy())
            finally:
                bitmap.close()
            return image
        finally:
            page.close()
    finally:
        document.close()


def render_preview(stream: IO[bytes], media_type: MediaType, max_side: int) -> bytes:
    """JPEG bytes of ``stream`` whose longest side is at most ``max_side`` pixels.

    Smaller images are not enlarged. Raises :class:`PreviewError` when the content cannot
    be decoded.
    """
    if media_type is MediaType.PDF:
        image = _render_pdf(stream, max_side)
    else:
        image = _open_image(stream)
    image = _to_jpeg_mode(image)
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    # A fresh image drops every info entry (EXIF, XMP, ICC, comments) of the original.
    clean = Image.new(image.mode, image.size)
    clean.paste(image)
    buffer = io.BytesIO()
    clean.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()
