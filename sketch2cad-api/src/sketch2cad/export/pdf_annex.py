"""Annex page of the technical PDF: the original (or rectified) image and its hash (spec 13.2).

The annex is exactly one page after the sheet, of the same paper size. Its header says
which image it is (``Anexo: croquis original`` or ``... rectificado``), the full SHA-256 of
the image bytes and the image size in pixels; the image is scaled to fit the rest of the
frame, keeping its aspect ratio. Evidence rows that did not fit on the sheet continue in a
column on the right (:mod:`sketch2cad.export.pdf_evidence`).

Embedding is deterministic: Pillow decodes the bytes (EXIF orientation is ignored, the
pixels are shown as stored), composites any transparency on white, converts to RGB and
downscales so the long side is at most :data:`MAX_SIDE_PX` with Lanczos resampling; ReportLab
then embeds the raw RGB pixels with its fixed Flate compression, so the same bytes give the
same PDF bytes.
"""

# The ReportLab stubs leave canvas callbacks partly unannotated (Unknown).
# pyright: reportUnknownMemberType=false

import hashlib
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from sketch2cad.export.pdf_evidence import CONTINUED_TITLE, draw_evidence_table
from sketch2cad.export.pdf_sheet import (
    FRAME_LINE_MM,
    LABEL_FONT,
    VALUE_FONT,
    Rect,
    SheetLayout,
    mm_to_pt,
)

MAX_SIDE_PX = 2000
HEADER_MM = 18.0
PADDING_MM = 3.0
CAPTION_MM = 4.0
TEXT_MM = 2.8
ANNEX_TITLES = {"original": "Anexo: croquis original", "rectified": "Anexo: croquis rectificado"}

AnnexLabel = Literal["original", "rectified"]


class AnnexImageError(ValueError):
    """The annex bytes are not a readable image or do not match their SHA-256."""


@dataclass(frozen=True)
class AnnexImage:
    """Image of the annex page: its bytes, their lowercase hex SHA-256 and what it is."""

    image_bytes: bytes
    sha256: str
    label: AnnexLabel = "original"


@dataclass(frozen=True)
class PreparedImage:
    """The image to embed and the size of the stored image in pixels."""

    image: Image.Image
    width_px: int
    height_px: int


def prepare_image(annex: AnnexImage) -> PreparedImage:
    """Checks the hash and decodes, flattens and downscales the image (module docstring)."""
    if hashlib.sha256(annex.image_bytes).hexdigest() != annex.sha256.lower():
        raise AnnexImageError("annex image bytes do not match their SHA-256")
    try:
        with Image.open(io.BytesIO(annex.image_bytes)) as source:
            source.load()
            width, height = source.size
            rgba = source.convert("RGBA")
    except (UnidentifiedImageError, OSError) as error:
        raise AnnexImageError(f"annex image cannot be decoded: {error}") from error
    flat = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
    longest = max(width, height)
    if longest > MAX_SIDE_PX:
        size = (round(width * MAX_SIDE_PX / longest), round(height * MAX_SIDE_PX / longest))
        flat = flat.resize((max(size[0], 1), max(size[1], 1)), Image.Resampling.LANCZOS)
    return PreparedImage(flat, width, height)


def header_lines(annex: AnnexImage, prepared: PreparedImage) -> list[str]:
    return [
        f"SHA-256: {annex.sha256.lower()}",
        f"Dimensiones: {prepared.width_px} × {prepared.height_px} px",
    ]


def fit_image(area: Rect, width_px: int, height_px: int) -> Rect:
    """The largest rectangle with the image aspect ratio centred in ``area``."""
    factor = min(area.width / width_px, area.height / height_px)
    width, height = width_px * factor, height_px * factor
    cx, cy = area.center
    return Rect(cx - width / 2, cy - height / 2, width, height)


def draw_annex_page(
    canvas: Canvas,
    layout: SheetLayout,
    annex: AnnexImage,
    overflow_rows: Sequence[Mapping[str, Any]] = (),
) -> None:
    """The annex on the current (new) page; the caller ends the page."""
    prepared = prepare_image(annex)
    frame = layout.frame
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setLineWidth(mm_to_pt(FRAME_LINE_MM))
    canvas.rect(*(mm_to_pt(v) for v in (frame.x, frame.y, frame.width, frame.height)))
    x = frame.x + PADDING_MM
    canvas.setFont(VALUE_FONT, mm_to_pt(CAPTION_MM))
    canvas.drawString(
        mm_to_pt(x), mm_to_pt(frame.top - PADDING_MM - CAPTION_MM), ANNEX_TITLES[annex.label]
    )
    canvas.setFont(LABEL_FONT, mm_to_pt(TEXT_MM))
    for index, line in enumerate(header_lines(annex, prepared)):
        baseline = frame.top - PADDING_MM - CAPTION_MM - (index + 1) * (TEXT_MM + 1.5)
        canvas.drawString(mm_to_pt(x), mm_to_pt(baseline), line)
    body_top = frame.top - HEADER_MM
    right = frame.right - PADDING_MM
    if overflow_rows:
        column = Rect(
            frame.right - layout.sheet.column_width_mm,
            frame.y + PADDING_MM,
            layout.sheet.column_width_mm - PADDING_MM,
            body_top - frame.y - PADDING_MM,
        )
        draw_evidence_table(canvas, column, overflow_rows, title=CONTINUED_TITLE)
        right = column.x - PADDING_MM
    area = Rect(x, frame.y + PADDING_MM, right - x, body_top - frame.y - PADDING_MM)
    box = fit_image(area, *prepared.image.size)
    x_pt, y_pt, width_pt, height_pt = (mm_to_pt(v) for v in (box.x, box.y, box.width, box.height))
    canvas.drawImage(ImageReader(prepared.image), x_pt, y_pt, width_pt, height_pt)
