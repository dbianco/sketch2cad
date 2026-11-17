"""300 dpi raster of single-page PDF sources, stored as a derived PNG object.

The PDF is never served to the browser as a document: page 1 is rendered with pypdfium2
and every later stage (preview, QC, calibration) works on the stored PNG. The original
stays untouched in storage; the raster is a separate write-once object referenced by
``source.raster_key``. Logs carry identifiers only.
"""

import io
import logging
import uuid
from decimal import Decimal
from typing import Any, NamedTuple, cast

import pypdfium2  # pyright: ignore[reportMissingTypeStubs]
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.db.models import MediaType, Source
from sketch2cad.storage import Storage

logger = logging.getLogger(__name__)

RASTER_DPI = 300
"""Resolution of the derived raster of PDF sources (spec: single-page PDFs at 300 dpi)."""

RASTER_MEDIA_TYPE = MediaType.PNG
_POINTS_PER_INCH = 72


class RasterError(ValueError):
    """The PDF cannot be rasterized (unreadable, not exactly one page or too large)."""


class SourceNotFoundError(LookupError):
    """No source exists with the requested id."""


class RasterImage(NamedTuple):
    """PNG bytes of a rendered page and its size in pixels."""

    png: bytes
    width_px: int
    height_px: int


def _render_first_page(document: Any, dpi: int) -> Image.Image:
    if len(document) != 1:
        raise RasterError("PDF does not have exactly one page")
    page = document[0]
    try:
        width_pt, height_pt = page.get_size()
        scale = dpi / _POINTS_PER_INCH
        # Same pixel budget that Pillow applies to decoded images (decompression bombs).
        if width_pt * scale * height_pt * scale > (Image.MAX_IMAGE_PIXELS or float("inf")):
            raise RasterError("PDF page is too large to rasterize")
        bitmap = page.render(scale=scale)
        try:
            # The PIL image may share the bitmap's buffer; copy it before closing.
            return cast(Image.Image, bitmap.to_pil().copy())
        finally:
            bitmap.close()
    finally:
        page.close()


def rasterize_pdf(pdf_bytes: bytes, dpi: int = RASTER_DPI) -> RasterImage:
    """Render the only page of ``pdf_bytes`` at ``dpi`` onto white and encode it as PNG.

    The PNG carries only its ``pHYs`` density (``dpi``), no other metadata. Raises
    :class:`RasterError` when the PDF is unreadable, has more or less than one page, or
    its page exceeds Pillow's ``MAX_IMAGE_PIXELS`` at ``dpi``.
    """
    try:
        document = pypdfium2.PdfDocument(pdf_bytes)
    except pypdfium2.PdfiumError:
        raise RasterError("unreadable PDF") from None
    try:
        image = _render_first_page(document, dpi)
    finally:
        document.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(dpi, dpi))
    return RasterImage(buffer.getvalue(), image.width, image.height)


def rasterize_source(db: Session, storage: Storage, source_id: uuid.UUID) -> Source:
    """Rasterize the PDF source ``source_id`` and record its raster in the caller's transaction.

    Locks the source row, stores the PNG with ``storage.put`` and sets ``raster_key``,
    ``width_px``, ``height_px`` and ``dpi`` (300) together; the caller commits. Image
    sources and PDFs that already have a raster are returned unchanged, so the call is
    idempotent. Storing before the update is safe: objects are write-once and content
    addressed, so a rolled-back transaction leaves at most an unreferenced object.
    """
    source = db.scalars(select(Source).where(Source.id == source_id).with_for_update()).first()
    if source is None:
        raise SourceNotFoundError(f"source {source_id} does not exist")
    if source.media_type != MediaType.PDF.value or source.raster_key is not None:
        return source
    raster = rasterize_pdf(storage.get(source.storage_key))
    stored = storage.put(io.BytesIO(raster.png))
    source.raster_key = stored.key
    source.width_px = raster.width_px
    source.height_px = raster.height_px
    source.dpi = Decimal(RASTER_DPI)
    db.flush()
    logger.info(
        "Rasterized source %s at %d dpi (%dx%d px)",
        source.id,
        RASTER_DPI,
        raster.width_px,
        raster.height_px,
    )
    return source
