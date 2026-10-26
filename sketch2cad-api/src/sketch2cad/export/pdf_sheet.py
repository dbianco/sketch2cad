"""Sheet geometry of the technical PDF: frame, drawing area and title block (spec 13.2).

All lengths are paper millimetres on a landscape sheet with the origin at its lower-left
corner and Y up, the PDF user space up to the factor :data:`PT_PER_MM`. The frame is drawn
:data:`MARGIN_MM` (15 mm) inside the sheet edges. Inside it, a column on the right holds the
title block at its bottom; the space above the title block (:attr:`SheetLayout.notes`) is
reserved for the scale bar, north arrow, legend and evidence table (task 088). The plan is
drawn in :attr:`SheetLayout.drawing_area`, the rest of the frame interior.

:class:`WorldToSheet` maps local-frame metres (Y up) to paper: ``paper_mm = world_m * 1000 /
scale``, with the centre of the plan extents on the centre of the drawing area.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

PT_PER_MM = 72.0 / 25.4
MARGIN_MM = 15.0
FRAME_LINE_MM = 0.5
TITLE_LINE_MM = 0.25
TITLE_ROW_MM = 7.0
TITLE_LABEL_MM = 28.0
TITLE_PADDING_MM = 1.5
LABEL_FONT = "Helvetica"
VALUE_FONT = "Helvetica-Bold"
LABEL_SIZE_MM = 2.2
VALUE_SIZE_MM = 2.8
ELLIPSIS = "…"
NO_VALUE = "—"
METRIC_STATUS_TEXT = {
    "metric": "Métrico",
    "approximate": "Aproximado",
    "non_metric": "No métrico",
}
SHORT_SHA_LENGTH = 12


def mm_to_pt(value_mm: float) -> float:
    """PDF points of a paper length in millimetres (1 mm = 72 / 25.4 pt)."""
    return value_mm * PT_PER_MM


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in paper millimetres; ``(x, y)`` is its lower-left corner."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


@dataclass(frozen=True)
class SheetSpec:
    """A landscape paper size and the width of its right-hand column."""

    name: str
    width_mm: float
    height_mm: float
    column_width_mm: float

    @property
    def size_pt(self) -> tuple[float, float]:
        return (mm_to_pt(self.width_mm), mm_to_pt(self.height_mm))


A4 = SheetSpec("A4", 297.0, 210.0, 80.0)
A3 = SheetSpec("A3", 420.0, 297.0, 100.0)
SHEETS: dict[str, SheetSpec] = {"A4": A4, "A3": A3}


@dataclass(frozen=True)
class SheetLayout:
    """Regions of one sheet (see the module docstring)."""

    sheet: SheetSpec
    frame: Rect
    drawing_area: Rect
    column: Rect
    title_block: Rect
    notes: Rect


def sheet_layout(sheet: SheetSpec, title_rows: int = 10) -> SheetLayout:
    """Layout of ``sheet`` with a title block of ``title_rows`` rows."""
    frame = Rect(
        MARGIN_MM, MARGIN_MM, sheet.width_mm - 2 * MARGIN_MM, sheet.height_mm - 2 * MARGIN_MM
    )
    column_x = frame.right - sheet.column_width_mm
    drawing_area = Rect(frame.x, frame.y, column_x - frame.x, frame.height)
    column = Rect(column_x, frame.y, sheet.column_width_mm, frame.height)
    title_height = title_rows * TITLE_ROW_MM
    title_block = Rect(column.x, column.y, column.width, title_height)
    notes = Rect(column.x, title_block.top, column.width, column.height - title_height)
    return SheetLayout(sheet, frame, drawing_area, column, title_block, notes)


@dataclass(frozen=True)
class WorldToSheet:
    """Similarity from local-frame metres to paper millimetres at ``1:scale``."""

    scale: int
    world_center: tuple[float, float]
    sheet_center: tuple[float, float]

    def length_mm(self, length_m: float) -> float:
        return length_m * 1000.0 / self.scale

    def point_mm(self, x_m: float, y_m: float) -> tuple[float, float]:
        return (
            self.sheet_center[0] + self.length_mm(x_m - self.world_center[0]),
            self.sheet_center[1] + self.length_mm(y_m - self.world_center[1]),
        )


def world_to_sheet(world_center: tuple[float, float], area: Rect, scale: int) -> WorldToSheet:
    """Transform that puts ``world_center`` (metres) on the centre of ``area``."""
    return WorldToSheet(scale, world_center, area.center)


@dataclass(frozen=True)
class CaseMeta:
    """Case data printed in the title block; the snapshot does not carry it."""

    case_code: str
    title: str
    incident_type: str
    incident_date: date | None
    technician: str
    reviewer: str | None


def title_block_rows(
    case_meta: CaseMeta,
    *,
    revision_number: int,
    status: str,
    sha256: str,
    scale: int,
    fits: bool = True,
) -> list[tuple[str, str]]:
    """``(label, value)`` rows of the title block, in Spanish, top to bottom."""
    incident_date = case_meta.incident_date
    scale_text = f"1:{scale}" if fits else f"1:{scale} (no cabe en la hoja)"
    return [
        ("Expediente", case_meta.case_code),
        ("Título", case_meta.title),
        ("Tipo de hecho", case_meta.incident_type),
        ("Fecha del hecho", incident_date.strftime("%d/%m/%Y") if incident_date else NO_VALUE),
        ("Técnico", case_meta.technician),
        ("Revisor", case_meta.reviewer or NO_VALUE),
        ("Revisión", str(revision_number)),
        ("Estado métrico", METRIC_STATUS_TEXT.get(status, status)),
        ("Snapshot SHA-256", sha256[:SHORT_SHA_LENGTH]),
        ("Escala", scale_text),
    ]


def fit_text(text: str, font: str, size_pt: float, width_pt: float) -> str:
    """``text`` cut with an ellipsis so that it is at most ``width_pt`` wide."""
    if stringWidth(text, font, size_pt) <= width_pt:
        return text
    while text and stringWidth(text + ELLIPSIS, font, size_pt) > width_pt:
        text = text[:-1]
    return text + ELLIPSIS


def draw_frame(canvas: Canvas, layout: SheetLayout) -> None:
    """The frame and the left edge of the right-hand column, in black."""
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(mm_to_pt(FRAME_LINE_MM))
    frame = layout.frame
    canvas.rect(*(mm_to_pt(v) for v in (frame.x, frame.y, frame.width, frame.height)))
    column = layout.column
    canvas.line(*(mm_to_pt(v) for v in (column.x, column.y, column.x, column.top)))


def draw_title_block(canvas: Canvas, layout: SheetLayout, rows: Sequence[tuple[str, str]]) -> None:
    """Rows of ``label`` and ``value`` from the top of the title block downwards."""
    block = layout.title_block
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setLineWidth(mm_to_pt(TITLE_LINE_MM))
    label_x = block.x + TITLE_PADDING_MM
    value_x = block.x + TITLE_LABEL_MM
    value_width = mm_to_pt(block.right - value_x - 2 * TITLE_PADDING_MM)
    for index, (label, value) in enumerate(rows):
        top = block.top - index * TITLE_ROW_MM
        canvas.line(*(mm_to_pt(v) for v in (block.x, top, block.right, top)))
        baseline = mm_to_pt(top - TITLE_ROW_MM + (TITLE_ROW_MM - VALUE_SIZE_MM) / 2.0 + 0.5)
        canvas.setFont(LABEL_FONT, mm_to_pt(LABEL_SIZE_MM))
        canvas.drawString(mm_to_pt(label_x), baseline, label)
        size = mm_to_pt(VALUE_SIZE_MM)
        canvas.setFont(VALUE_FONT, size)
        canvas.drawString(
            mm_to_pt(value_x + TITLE_PADDING_MM),
            baseline,
            fit_text(value, VALUE_FONT, size, value_width),
        )
    canvas.line(*(mm_to_pt(v) for v in (value_x, block.y, value_x, block.top)))
