"""Diagonal ``BORRADOR / DRAFT`` watermark of draft PDF pages (spec 13.2, REQ-13).

The mark is drawn last on every page of a draft (the sheet and the annex), centred on the
sheet and rotated along its lower-left to upper-right diagonal. It is large (its width is
:data:`WATERMARK_WIDTH_FRACTION` of the diagonal) and light: a grey fill at
:data:`WATERMARK_ALPHA` opacity, so the drawing, the title block and the image underneath
stay readable. ReportLab writes the opacity as a fixed ExtGState and the text as plain
Helvetica-Bold operators, so the bytes are deterministic. Final exports never call it.
"""

# The ReportLab stubs leave canvas callbacks partly unannotated (Unknown).
# pyright: reportUnknownMemberType=false

import math

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from sketch2cad.export.pdf_sheet import SheetSpec, mm_to_pt

WATERMARK_TEXT = "BORRADOR / DRAFT"
WATERMARK_FONT = "Helvetica-Bold"
WATERMARK_GREY = 0.55
WATERMARK_ALPHA = 0.18
WATERMARK_WIDTH_FRACTION = 0.7


def watermark_angle_deg(sheet: SheetSpec) -> float:
    """Angle of the sheet diagonal from the horizontal (about 35 degrees on A4 and A3)."""
    return math.degrees(math.atan2(sheet.height_mm, sheet.width_mm))


def watermark_font_size_pt(sheet: SheetSpec) -> float:
    """Font size that makes the mark :data:`WATERMARK_WIDTH_FRACTION` of the diagonal."""
    diagonal_pt = mm_to_pt(math.hypot(sheet.width_mm, sheet.height_mm))
    return WATERMARK_WIDTH_FRACTION * diagonal_pt / stringWidth(WATERMARK_TEXT, WATERMARK_FONT, 1)


def draw_watermark(canvas: Canvas, sheet: SheetSpec) -> None:
    """The translucent diagonal mark across the current page of size ``sheet``."""
    size = watermark_font_size_pt(sheet)
    canvas.saveState()
    canvas.translate(mm_to_pt(sheet.width_mm / 2.0), mm_to_pt(sheet.height_mm / 2.0))
    canvas.rotate(watermark_angle_deg(sheet))
    canvas.setFillColorRGB(WATERMARK_GREY, WATERMARK_GREY, WATERMARK_GREY)
    canvas.setFillAlpha(WATERMARK_ALPHA)
    canvas.setFont(WATERMARK_FONT, size)
    # Centre the cap height on the diagonal (Helvetica caps are about 0.72 em).
    canvas.drawCentredString(0, -0.36 * size, WATERMARK_TEXT)
    canvas.restoreState()
