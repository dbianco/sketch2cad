"""Evidence table of the technical PDF: number, label, X, Y and metric origin (spec 13.2).

Rows come from the snapshot ``evidence_table`` (spec 8.3), already sorted by number. Texts
follow the web evidence table (``features/evidence/messages.ts``): Spanish headers and
metric-origin labels, coordinates in local-frame metres with two decimals and a decimal
comma (``-0,00`` prints ``0,00``). Long labels are cut with an ellipsis.

:func:`draw_evidence_table` draws as many rows as fit in its area; when some do not fit, the
last line says how many are left and where they continue (the annex page, see
:mod:`sketch2cad.export.pdf_annex`, or nowhere when there is no annex). The table never adds
pages of its own.
"""

# The ReportLab stubs leave canvas callbacks partly unannotated (Unknown).
# pyright: reportUnknownMemberType=false

import math
from collections.abc import Mapping, Sequence
from typing import Any

from reportlab.pdfgen.canvas import Canvas

from sketch2cad.export.pdf_sheet import LABEL_FONT, VALUE_FONT, Rect, fit_text, mm_to_pt

TITLE = "Tabla de indicios"
CONTINUED_TITLE = "Tabla de indicios (continuación)"
EMPTY_TEXT = "Sin indicios ubicados en el plano."
HEADERS = ("Nº", "Etiqueta", "X (m)", "Y (m)", "Origen métrico")
COLUMN_FRACTIONS = (0.09, 0.33, 0.14, 0.14, 0.30)
METRIC_ORIGIN_TEXT = {
    "declared_dimension": "Cota declarada",
    "triangulation": "Triangulación",
    "baseline_offset": "Base y offset",
    "scaled_drawing": "Dibujo a escala",
    "derived": "Derivado",
    "trace_only": "Solo trazo",
    "none": "Sin origen métrico",
}
TITLE_MM = 2.8
TEXT_MM = 2.0
TITLE_ROW_MM = 5.0
ROW_MM = 3.5
LINE_MM = 0.18
CELL_PADDING_MM = 0.8


def metres_text(value: Any) -> str:
    """Coordinate with two decimals and a decimal comma, as the web table shows it."""
    text = f"{float(value):.2f}"
    return ("0.00" if text == "-0.00" else text).replace(".", ",")


def row_cells(row: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    origin = str(row.get("metric_origin"))
    return (
        str(row["evidence_number"]),
        str(row["label"]),
        metres_text(row["x_m"]),
        metres_text(row["y_m"]),
        METRIC_ORIGIN_TEXT.get(origin, origin),
    )


def rows_that_fit(height_mm: float, count: int) -> int:
    """Rows drawn in ``height_mm``: all of them, or one line fewer for the overflow note."""
    capacity = max(math.floor((height_mm - TITLE_ROW_MM - ROW_MM) / ROW_MM + 1e-9), 0)
    return count if count <= capacity else max(capacity - 1, 0)


def overflow_text(left: int, on_annex: bool) -> str:
    where = "continúan en el anexo" if on_annex else "no caben en la hoja"
    return f"… {left} indicios más {where}."


def draw_evidence_table(
    canvas: Canvas,
    area: Rect,
    rows: Sequence[Mapping[str, Any]],
    *,
    title: str = TITLE,
    overflow_on_annex: bool = False,
) -> int:
    """The title, headers and the rows that fit in ``area``; returns how many were drawn."""
    shown = rows_that_fit(area.height, len(rows))
    canvas.saveState()
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setLineWidth(mm_to_pt(LINE_MM))
    canvas.setFont(VALUE_FONT, mm_to_pt(TITLE_MM))
    canvas.drawString(mm_to_pt(area.x), mm_to_pt(area.top - TITLE_MM - 1.0), title)
    top = area.top - TITLE_ROW_MM
    if not rows:
        canvas.setFont(LABEL_FONT, mm_to_pt(TEXT_MM))
        canvas.drawString(mm_to_pt(area.x), mm_to_pt(top - ROW_MM + 1.0), EMPTY_TEXT)
        canvas.restoreState()
        return 0
    widths = [fraction * area.width for fraction in COLUMN_FRACTIONS]
    lines = [(HEADERS, VALUE_FONT), *((row_cells(row), LABEL_FONT) for row in rows[:shown])]
    for index, (cells, font) in enumerate(lines):
        baseline = top - (index + 1) * ROW_MM + 1.0
        canvas.setFont(font, mm_to_pt(TEXT_MM))
        x = area.x
        for cell, width in zip(cells, widths, strict=True):
            room = mm_to_pt(width - 2 * CELL_PADDING_MM)
            text = fit_text(cell, font, mm_to_pt(TEXT_MM), room)
            canvas.drawString(mm_to_pt(x + CELL_PADDING_MM), mm_to_pt(baseline), text)
            x += width
        if index == 0:
            y = mm_to_pt(top - ROW_MM)
            canvas.line(mm_to_pt(area.x), y, mm_to_pt(area.right), y)
    if shown < len(rows):
        canvas.setFont(LABEL_FONT, mm_to_pt(TEXT_MM))
        baseline = top - (shown + 2) * ROW_MM + 1.0
        text = overflow_text(len(rows) - shown, overflow_on_annex)
        canvas.drawString(mm_to_pt(area.x), mm_to_pt(baseline), text)
    canvas.restoreState()
    return shown
