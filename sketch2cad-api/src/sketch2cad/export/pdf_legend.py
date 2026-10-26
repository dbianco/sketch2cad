"""Graphic scale bar, north arrow and legend of used symbols in the PDF notes (spec 13.2).

The notes area (:attr:`~sketch2cad.export.pdf_sheet.SheetLayout.notes`, the column above the
title block) is split by :func:`notes_layout`: a top band with the north arrow on the left
and the scale bar on its right, then the legend, then the evidence table
(:mod:`sketch2cad.export.pdf_evidence`) down to the bottom of the area.

Scale bar: the longest of 1, 2 or 5 x 10^k metres that is at most
:data:`SCALE_BAR_MAX_MM` long on paper (5 m = 50 mm at 1:100), divided in alternating
black and white parts with a label in metres (decimal comma) at every division.

North arrow: the catalog ``north_arrow`` block, whose tip points to block +Y. The sheet
draws the local frame with local +Y up, and ``north_angle_deg`` is measured from local
+Y counter-clockwise, so the block is rotated by ``north_angle_deg`` counter-clockwise:
North points along ``(-sin a, cos a)`` on the sheet. Without a local frame the North is
unknown and the box says so instead of drawing an arrow.

Symbol glyphs are drawn by the caller's :data:`GlyphDrawer` (``pdf.draw_symbol``), which
keeps this module free of an import cycle with :mod:`sketch2cad.export.pdf`.
"""

# The ReportLab stubs leave canvas callbacks partly unannotated (Unknown).
# pyright: reportUnknownMemberType=false

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from reportlab.pdfgen.canvas import Canvas

from sketch2cad.catalog import CatalogDefinition, Symbol
from sketch2cad.catalog.schema import DxfArc, DxfCircle, DxfLine, DxfPolyline
from sketch2cad.export.dxf_symbols import EVIDENCE_MARKER, find_symbol
from sketch2cad.export.pdf_sheet import LABEL_FONT, VALUE_FONT, Rect, fit_text, mm_to_pt

NORTH_ARROW = "north_arrow"
PADDING_MM = 2.5
TOP_BAND_MM = 22.0
NORTH_BOX_MM = 18.0
LABEL_ROOM_MM = 2.0
SCALE_BAR_MAX_MM = 50.0
SCALE_BAR_HEIGHT_MM = 2.0
SCALE_BAR_LINE_MM = 0.2
NICE_LEADS = (1, 2, 5)
DIVISIONS = {1: 5, 2: 4, 5: 5}
TEXT_MM = 2.2
TITLE_MM = 2.8
LEGEND_TITLE_MM = 5.0
LEGEND_ROW_MM = 5.0
LEGEND_GLYPH_MM = 4.0
LEGEND_COLUMNS = 2
NO_NORTH_TEXT = "Norte sin definir"

Point = tuple[float, float]
GlyphDrawer = Callable[[Canvas, Symbol, Point, float, float, Mapping[str, Any]], None]


@dataclass(frozen=True)
class NotesLayout:
    """Regions of the notes area, in paper millimetres."""

    north: Rect
    scale_bar: Rect
    legend: Rect
    evidence: Rect


def legend_rows(symbol_count: int) -> int:
    return math.ceil(symbol_count / LEGEND_COLUMNS)


def notes_layout(notes: Rect, symbol_count: int) -> NotesLayout:
    """Split ``notes`` for a legend of ``symbol_count`` symbols (none: no legend)."""
    inner_x, inner_width = notes.x + PADDING_MM, notes.width - 2 * PADDING_MM
    band_y = notes.top - PADDING_MM - TOP_BAND_MM
    north = Rect(inner_x, band_y + (TOP_BAND_MM - NORTH_BOX_MM) / 2, NORTH_BOX_MM, NORTH_BOX_MM)
    bar_x = north.right + PADDING_MM
    scale_bar = Rect(bar_x, band_y, inner_x + inner_width - bar_x, TOP_BAND_MM)
    rows = legend_rows(symbol_count)
    legend_height = LEGEND_TITLE_MM + rows * LEGEND_ROW_MM if rows else 0.0
    legend = Rect(inner_x, band_y - legend_height, inner_width, legend_height)
    bottom = notes.y + PADDING_MM
    evidence = Rect(inner_x, bottom, inner_width, max(legend.y - PADDING_MM - bottom, 0.0))
    return NotesLayout(north, scale_bar, legend, evidence)


def scale_bar_length_m(scale: int, max_mm: float = SCALE_BAR_MAX_MM) -> float:
    """Longest 1, 2 or 5 x 10^k metres that is at most ``max_mm`` on paper at ``1:scale``."""
    max_m = max_mm * scale / 1000.0
    exponent = math.floor(math.log10(max_m))
    candidates = [lead * 10.0**k for k in (exponent - 1, exponent) for lead in NICE_LEADS]
    return max(length for length in candidates if length <= max_m + 1e-9)


def metres_text(value: float) -> str:
    """``value`` without trailing zeros and with a decimal comma (``0,5``, ``10``)."""
    return f"{round(value, 6):g}".replace(".", ",")


def _rect(canvas: Canvas, rect: Rect, *, stroke: int, fill: int) -> None:
    x, y, width, height = (mm_to_pt(v) for v in (rect.x, rect.y, rect.width, rect.height))
    canvas.rect(x, y, width, height, stroke=stroke, fill=fill)


def draw_scale_bar(canvas: Canvas, area: Rect, scale: int) -> float:
    """The bar at the left of ``area`` (after room for the first label); returns its length
    on paper in millimetres."""
    left = area.x + LABEL_ROOM_MM
    length_m = scale_bar_length_m(scale, min(SCALE_BAR_MAX_MM, area.width - 2 * LABEL_ROOM_MM))
    lead = round(length_m / 10.0 ** math.floor(math.log10(length_m)))
    parts = DIVISIONS[lead]
    length_mm = length_m * 1000.0 / scale
    part_mm = length_mm / parts
    bar_y = area.y + area.height / 2 - SCALE_BAR_HEIGHT_MM / 2
    canvas.saveState()
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setLineWidth(mm_to_pt(SCALE_BAR_LINE_MM))
    for index in range(0, parts, 2):
        x = left + index * part_mm
        _rect(canvas, Rect(x, bar_y, part_mm, SCALE_BAR_HEIGHT_MM), stroke=0, fill=1)
    _rect(canvas, Rect(left, bar_y, length_mm, SCALE_BAR_HEIGHT_MM), stroke=1, fill=0)
    canvas.setFont(LABEL_FONT, mm_to_pt(TEXT_MM))
    label_y = mm_to_pt(bar_y + SCALE_BAR_HEIGHT_MM + 1.0)
    for index in range(parts + 1):
        text = metres_text(length_m * index / parts) + (" m" if index == parts else "")
        canvas.drawCentredString(mm_to_pt(left + index * part_mm), label_y, text)
    canvas.drawString(mm_to_pt(left), mm_to_pt(bar_y - 1.0 - TEXT_MM), "Escala gráfica")
    canvas.restoreState()
    return length_mm


def north_direction(north_angle_deg: float) -> Point:
    """Unit vector of North on the sheet (see the module docstring)."""
    angle = math.radians(north_angle_deg)
    return (-math.sin(angle), math.cos(angle))


def draw_north_arrow(
    canvas: Canvas,
    area: Rect,
    catalog: CatalogDefinition,
    local_frame: Mapping[str, Any] | None,
    draw: GlyphDrawer,
) -> None:
    """The catalog north arrow rotated to ``local_frame.north_angle_deg`` in ``area``."""
    canvas.saveState()
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setLineWidth(mm_to_pt(SCALE_BAR_LINE_MM))
    cx, cy = area.center
    if local_frame is None:
        canvas.setFont(LABEL_FONT, mm_to_pt(TEXT_MM))
        canvas.drawCentredString(mm_to_pt(cx), mm_to_pt(cy), NO_NORTH_TEXT)
    else:
        symbol = find_symbol(catalog, NORTH_ARROW)
        size = min(symbol.default_size, area.height / (2 * block_radius(symbol)))
        angle = float(local_frame["north_angle_deg"])
        draw(canvas, symbol, (mm_to_pt(cx), mm_to_pt(cy)), angle, size, {})
    canvas.restoreState()


def used_symbols(snapshot: Mapping[str, Any], catalog: CatalogDefinition) -> list[Symbol]:
    """Catalog symbols drawn on the plan, in catalog order: symbol entities and the
    evidence marker of points that place evidence."""
    used: set[str] = set()
    for entity in snapshot["entities"]:
        if entity["type"] == "symbol":
            used.add(str(entity["geometry"]["symbol_id"]))
        elif entity["type"] == "point" and entity.get("evidence_id") is not None:
            used.add(EVIDENCE_MARKER)
    return [symbol for symbol in catalog.symbols if symbol.symbol_id in used]


def block_radius(symbol: Symbol) -> float:
    """Largest distance in block units from the base point to a primitive (at least 0.5)."""
    bx, by = symbol.dxf_block.base_point
    radius = 0.5
    for item in symbol.dxf_block.entities:
        if isinstance(item, DxfLine):
            points, extra = [item.start, item.end], 0.0
        elif isinstance(item, DxfPolyline):
            points, extra = list(item.points), 0.0
        elif isinstance(item, DxfCircle | DxfArc):
            points, extra = [item.center], item.radius
        else:
            continue
        for x, y in points:
            radius = max(radius, math.hypot(x - bx, y - by) + extra)
    return radius


def draw_legend(canvas: Canvas, area: Rect, symbols: Sequence[Symbol], draw: GlyphDrawer) -> None:
    """``Leyenda``: glyph and catalog label of every used symbol, in two columns."""
    if not symbols:
        return
    canvas.saveState()
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setLineWidth(mm_to_pt(SCALE_BAR_LINE_MM))
    canvas.setFont(VALUE_FONT, mm_to_pt(TITLE_MM))
    canvas.drawString(mm_to_pt(area.x), mm_to_pt(area.top - TITLE_MM - 1.0), "Leyenda")
    column_mm = area.width / LEGEND_COLUMNS
    label_width = mm_to_pt(column_mm - LEGEND_GLYPH_MM - 2.0)
    for index, symbol in enumerate(symbols):
        row, column = divmod(index, LEGEND_COLUMNS)
        x = area.x + column * column_mm
        cy = area.top - LEGEND_TITLE_MM - row * LEGEND_ROW_MM - LEGEND_ROW_MM / 2
        factor = LEGEND_GLYPH_MM / (2 * block_radius(symbol))
        anchor = (mm_to_pt(x + LEGEND_GLYPH_MM / 2), mm_to_pt(cy))
        draw(canvas, symbol, anchor, 0.0, factor, {})
        canvas.setFont(LABEL_FONT, mm_to_pt(TEXT_MM))
        label = fit_text(symbol.label, LABEL_FONT, mm_to_pt(TEXT_MM), label_width)
        canvas.drawString(mm_to_pt(x + LEGEND_GLYPH_MM + 1.5), mm_to_pt(cy - TEXT_MM / 3), label)
    canvas.restoreState()
