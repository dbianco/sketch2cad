"""Technical PDF of a revision snapshot: sheet, scale, title block and plan (spec 13.2, REQ-13).

:func:`export_pdf` is a pure function, like :func:`~sketch2cad.export.dxf.export_dxf`: it
reads only the snapshot dict, its catalog, the case data of the title block
(:class:`~sketch2cad.export.pdf_sheet.CaseMeta`) and ``frozen_at``, and returns the bytes.

Steps: :func:`plan_page` measures the plan (:func:`plan_bounds`) and picks the sheet and the
standard scale (:mod:`sketch2cad.export.scale`); :func:`new_canvas` opens a ReportLab canvas
whose every save-time value is fixed; the frame, the plan (clipped to the drawing area) and
the title block are drawn; task 088 adds scale bar, north arrow, legend and evidence table
in :attr:`SheetLayout.notes`, task 089 the watermark.

Each snapshot entity type maps to a drawing function in :data:`ENTITY_DRAWERS` (the PDF
twin of the DXF ``ENTITY_WRITERS``). Lines keep their real length: a 1 m line at 1:100 is
10 mm (28.35 pt). Symbols are drawn from the catalog ``dxf_block`` primitives, scaled as
in the DXF (``paper_mm`` sizes are paper millimetres, ``world_m`` sizes metres).

Reproducibility (CA-08): ``invariant=1`` and an uncompressed content stream (zlib output
may differ between builds); ``/CreationDate`` and ``/ModDate`` come from ``frozen_at``
(:data:`~sketch2cad.export.dxf_repro.DRAFT_EPOCH` for drafts) through the date formatter;
the document ``/ID`` is seeded from the plan id and the snapshot hash instead of ReportLab's
timestamp, which ``SOURCE_DATE_EPOCH`` would otherwise change.
"""

# The ReportLab stubs leave path and canvas callbacks partly unannotated (Unknown).
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import hashlib
import io
import logging
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas

from sketch2cad.catalog import CatalogDefinition, Symbol
from sketch2cad.catalog.schema import DxfArc, DxfCircle, DxfLine, DxfPolyline
from sketch2cad.entities.dimensions import (
    DimensionTolerances,
    dimension_discrepancy,
    measured_length,
)
from sketch2cad.export.dxf_dimensions import MIN_LENGTH_M, dimension_text
from sketch2cad.export.dxf_repro import to_utc
from sketch2cad.export.dxf_symbols import (
    EVIDENCE_MARKER,
    UnknownSymbolError,
    attribute_text,
    evidence_marker_attributes,
    find_symbol,
)
from sketch2cad.export.pdf_sheet import (
    SHEETS,
    CaseMeta,
    SheetLayout,
    WorldToSheet,
    draw_frame,
    draw_title_block,
    mm_to_pt,
    sheet_layout,
    title_block_rows,
    world_to_sheet,
)
from sketch2cad.export.scale import (
    STANDARD_SCALES,
    Bounds,
    ScaleProposal,
    fits_at,
    propose_scale,
    propose_sheet,
)
from sketch2cad.revisions.snapshot import snapshot_sha256

logger = logging.getLogger(__name__)

SheetChoice = Literal["A4", "A3", "auto"]
DOCUMENT_LANG = "es-ES"
TEXT_FONT = "Helvetica"
# Print colours of the ACI indices, as the editor shows them on white (web ``layers.ts``).
ACI_PRINT_COLORS: dict[int, str] = {
    1: "#e00000",
    2: "#b39200",
    3: "#00a000",
    4: "#008b9a",
    5: "#0030e0",
    6: "#c000c0",
    7: "#000000",
    8: "#808080",
    9: "#a0a0a0",
}
FALLBACK_COLOR = "#606060"
ALERT_COLOR = ACI_PRINT_COLORS[1]
LINE_WIDTH_MM = 0.25
LAYER_LINE_WIDTH_MM: dict[str, float] = {"L_STRUCTURE": 0.5, "L_DIMENSIONS": 0.18}
POINT_RADIUS_MM = 0.5
DIM_TEXT_MM = 2.5
DIM_TICK_MM = 2.5
DIM_EXTENSION_BEYOND_MM = 1.25
DIM_TEXT_GAP_MM = 0.625

Point = tuple[float, float]


class PdfExportError(ValueError):
    """The snapshot cannot be drawn (unknown layer, type or symbol) or the options are invalid."""


@dataclass(frozen=True)
class PdfContext:
    """What drawing functions need besides the entity."""

    catalog: CatalogDefinition
    transform: WorldToSheet
    layer_colors: Mapping[str, str]
    evidence_table: Sequence[Mapping[str, Any]]
    dimension_tolerances: DimensionTolerances

    def pt(self, x_m: float, y_m: float) -> Point:
        x_mm, y_mm = self.transform.point_mm(x_m, y_m)
        return (mm_to_pt(x_mm), mm_to_pt(y_mm))


@dataclass(frozen=True)
class PagePlan:
    """Sheet layout, chosen scale and world-to-sheet transform of one export."""

    layout: SheetLayout
    proposal: ScaleProposal
    transform: WorldToSheet
    bounds: Bounds | None


EntityDrawer = Callable[[Canvas, Mapping[str, Any], PdfContext], None]


def aci_print_color(aci: int) -> str:
    return ACI_PRINT_COLORS.get(aci, FALLBACK_COLOR)


def _xy(value: Sequence[Any]) -> Point:
    return (float(value[0]), float(value[1]))


def _rotate(x: float, y: float, degrees: float) -> Point:
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    return (x * cos - y * sin, x * sin + y * cos)


def symbol_factor_mm(symbol: Symbol, scale: float, transform: WorldToSheet) -> float:
    """Paper millimetres per block unit of ``symbol`` drawn at entity ``scale``."""
    size = symbol.default_size * scale
    return size if symbol.size_mode == "paper_mm" else transform.length_mm(size)


def _block_points(symbol: Symbol) -> Iterator[tuple[Point, float]]:
    """``(point, radius)`` of every primitive of the block, in block units from its base."""
    bx, by = symbol.dxf_block.base_point
    for item in symbol.dxf_block.entities:
        if isinstance(item, DxfLine):
            points, radius = [item.start, item.end], 0.0
        elif isinstance(item, DxfPolyline):
            points, radius = list(item.points), 0.0
        elif isinstance(item, DxfCircle | DxfArc):
            points, radius = [item.center], item.radius
        else:
            continue
        for x, y in points:
            yield (float(x) - bx, float(y) - by), float(radius)


def _entity_points(entity: Mapping[str, Any], catalog: CatalogDefinition) -> Iterator[Point]:
    geometry = entity["geometry"]
    kind = entity["type"]
    if kind in ("line", "polyline", "point"):
        yield from (_xy(c) for c in geometry["coords_m"])
    elif kind == "text":
        yield _xy(geometry["anchor_m"])
    elif kind == "dimension":
        (x1, y1), (x2, y2) = _xy(geometry["p1_m"]), _xy(geometry["p2_m"])
        length = math.hypot(x2 - x1, y2 - y1)
        offset = float(geometry.get("offset_m", 0.0))
        nx, ny = ((y1 - y2) / length, (x2 - x1) / length) if length else (0.0, 0.0)
        yield from ((x1, y1), (x2, y2), (x1 + nx * offset, y1 + ny * offset))
        yield (x2 + nx * offset, y2 + ny * offset)
    elif kind == "symbol":
        ax, ay = _xy(geometry["anchor_m"])
        yield (ax, ay)
        symbol = find_symbol(catalog, geometry["symbol_id"])
        if symbol.size_mode == "world_m":
            size = symbol.default_size * float(geometry.get("scale", 1.0))
            rotation = float(geometry.get("rotation_deg", 0.0))
            for (px, py), radius in _block_points(symbol):
                rx, ry = _rotate(px * size, py * size, rotation)
                for dx, dy in ((-1, -1), (1, 1)):
                    yield (ax + rx + dx * radius * size, ay + ry + dy * radius * size)


def plan_bounds(snapshot: Mapping[str, Any], catalog: CatalogDefinition) -> Bounds | None:
    """Extents in metres of the snapshot geometry, ``None`` for an empty plan.

    ``world_m`` symbols count with their block; ``paper_mm`` symbols and texts only with
    their anchor, since their size in metres depends on the scale being chosen.
    """
    xs: list[float] = []
    ys: list[float] = []
    for entity in snapshot["entities"]:
        try:
            for x, y in _entity_points(entity, catalog):
                xs.append(x)
                ys.append(y)
        except UnknownSymbolError as error:
            raise PdfExportError(f"entity {entity['id']}: {error}") from error
    if not xs:
        return None
    return Bounds(min(xs), min(ys), max(xs), max(ys))


def plan_page(
    snapshot: Mapping[str, Any],
    catalog: CatalogDefinition,
    *,
    sheet: SheetChoice = "auto",
    scale: int | None = None,
) -> PagePlan:
    """Sheet and scale of the export: the proposal unless the caller fixes them.

    A fixed ``scale`` is kept even when the plan does not fit (``proposal.fits`` False, the
    drawing is clipped to the drawing area); it must be one of :data:`STANDARD_SCALES`.
    """
    if scale is not None and scale not in STANDARD_SCALES:
        raise PdfExportError(f"scale 1:{scale} is not one of {STANDARD_SCALES}")
    if sheet != "auto" and sheet not in SHEETS:
        raise PdfExportError(f"unknown sheet {sheet!r}")
    bounds = plan_bounds(snapshot, catalog)
    candidates = [SHEETS["A4"], SHEETS["A3"]] if sheet == "auto" else [SHEETS[sheet]]
    if scale is None:
        if sheet == "auto":
            spec, proposal = propose_sheet(bounds)
        else:
            spec, proposal = candidates[0], propose_scale(bounds, candidates[0])
    else:
        fitting = [s for s in candidates if fits_at(bounds, sheet_layout(s).drawing_area, scale)]
        spec = fitting[0] if fitting else candidates[-1]
        proposal = ScaleProposal(scale, bool(fitting))
    layout = sheet_layout(spec)
    center = bounds.center if bounds else (0.0, 0.0)
    transform = world_to_sheet(center, layout.drawing_area, proposal.scale)
    return PagePlan(layout, proposal, transform, bounds)


def pdf_date(frozen_at: datetime | None) -> str:
    """PDF date string of ``frozen_at`` in UTC (``D:20261021093015+00'00'``)."""
    return to_utc(frozen_at).strftime("D:%Y%m%d%H%M%S+00'00'")


def new_canvas(
    buffer: io.BytesIO,
    layout: SheetLayout,
    *,
    title: str,
    subject: str,
    creator: str,
    frozen_at: datetime | None,
    id_seed: str,
) -> Canvas:
    """A one-sheet canvas whose dates, ``/ID`` and metadata depend only on the arguments."""
    canvas = Canvas(
        buffer,
        pagesize=layout.sheet.size_pt,
        pageCompression=0,
        invariant=1,
        lang=DOCUMENT_LANG,
    )
    date = pdf_date(frozen_at)
    canvas.setDateFormatter(lambda *_parts: date)  # pyright: ignore[reportUnknownLambdaType]
    # ReportLab seeds the /ID digest with its timestamp, which SOURCE_DATE_EPOCH can change.
    document = canvas._doc  # pyright: ignore[reportAttributeAccessIssue,reportPrivateUsage]
    document.signature = hashlib.sha256(id_seed.encode("utf-8"))
    canvas.setTitle(title)
    canvas.setAuthor("Sketch2CAD")
    canvas.setSubject(subject)
    canvas.setCreator(creator)
    canvas.setKeywords(subject)
    return canvas


def _set_style(canvas: Canvas, entity: Mapping[str, Any], context: PdfContext) -> None:
    color = HexColor(context.layer_colors.get(entity["layer"], FALLBACK_COLOR))
    canvas.setStrokeColor(color)
    canvas.setFillColor(color)
    canvas.setLineWidth(mm_to_pt(LAYER_LINE_WIDTH_MM.get(entity["layer"], LINE_WIDTH_MM)))


def _stroke(canvas: Canvas, points: Sequence[Point], closed: bool = False) -> None:
    path = canvas.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    if closed:
        path.close()
    canvas.drawPath(path, stroke=1, fill=0)


def _text(
    canvas: Canvas, content: str, at: Point, height_mm: float, rotation_deg: float, centred: bool
) -> None:
    canvas.saveState()
    canvas.translate(*at)
    canvas.rotate(rotation_deg)
    canvas.setFont(TEXT_FONT, mm_to_pt(height_mm))
    if centred:
        canvas.drawCentredString(0, 0, content)
    else:
        canvas.drawString(0, 0, content)
    canvas.restoreState()


def _draw_line(canvas: Canvas, entity: Mapping[str, Any], context: PdfContext) -> None:
    _set_style(canvas, entity, context)
    _stroke(canvas, [context.pt(*_xy(c)) for c in entity["geometry"]["coords_m"]])


def _draw_polyline(canvas: Canvas, entity: Mapping[str, Any], context: PdfContext) -> None:
    geometry = entity["geometry"]
    _set_style(canvas, entity, context)
    points = [context.pt(*_xy(c)) for c in geometry["coords_m"]]
    _stroke(canvas, points, bool(geometry.get("closed", False)))


def _draw_point(canvas: Canvas, entity: Mapping[str, Any], context: PdfContext) -> None:
    """A dot, plus the evidence marker when the point places evidence."""
    _set_style(canvas, entity, context)
    ((x, y),) = entity["geometry"]["coords_m"]
    at = context.pt(float(x), float(y))
    canvas.circle(*at, mm_to_pt(POINT_RADIUS_MM), stroke=0, fill=1)
    if entity.get("evidence_id") is not None:
        symbol = find_symbol(context.catalog, EVIDENCE_MARKER)
        attributes = evidence_marker_attributes(entity, context.evidence_table)
        draw_symbol(
            canvas, symbol, at, 0.0, symbol_factor_mm(symbol, 1.0, context.transform), attributes
        )


def _draw_text(canvas: Canvas, entity: Mapping[str, Any], context: PdfContext) -> None:
    geometry = entity["geometry"]
    _set_style(canvas, entity, context)
    at = context.pt(*_xy(geometry["anchor_m"]))
    rotation = float(geometry.get("rotation_deg", 0.0))
    _text(canvas, str(geometry["content"]), at, float(geometry["height_mm_paper"]), rotation, False)


def _draw_dimension(canvas: Canvas, entity: Mapping[str, Any], context: PdfContext) -> None:
    """Dimension line offset from ``p1 -> p2``, extension lines, ticks and the value text."""
    geometry = entity["geometry"]
    p1, p2 = _xy(geometry["p1_m"]), _xy(geometry["p2_m"])
    measured = measured_length(p1, p2)
    if measured < MIN_LENGTH_M:
        raise PdfExportError(f"entity {entity['id']}: dimension points p1_m and p2_m coincide")
    _set_style(canvas, entity, context)
    if dimension_discrepancy(entity["type"], geometry, context.dimension_tolerances):
        canvas.setStrokeColor(HexColor(ALERT_COLOR))
        canvas.setFillColor(HexColor(ALERT_COLOR))
    a, b = context.transform.point_mm(*p1), context.transform.point_mm(*p2)
    ux, uy = (b[0] - a[0]) / math.dist(a, b), (b[1] - a[1]) / math.dist(a, b)
    nx, ny = -uy, ux
    offset = context.transform.length_mm(float(geometry.get("offset_m", 0.0)))
    side = math.copysign(1.0, offset) if offset else 1.0
    d1 = (a[0] + nx * offset, a[1] + ny * offset)
    d2 = (b[0] + nx * offset, b[1] + ny * offset)
    beyond = side * DIM_EXTENSION_BEYOND_MM

    def pt(x: float, y: float) -> Point:
        return (mm_to_pt(x), mm_to_pt(y))

    _stroke(canvas, [pt(*d1), pt(*d2)])
    if offset:
        for base, end in ((a, d1), (b, d2)):
            _stroke(canvas, [pt(*base), pt(end[0] + nx * beyond, end[1] + ny * beyond)])
    tx, ty = (ux + nx) * DIM_TICK_MM / 2 / math.sqrt(2), (uy + ny) * DIM_TICK_MM / 2 / math.sqrt(2)
    for x, y in (d1, d2):
        _stroke(canvas, [pt(x - tx, y - ty), pt(x + tx, y + ty)])
    angle = math.degrees(math.atan2(uy, ux))
    if angle > 90.0 or angle <= -90.0:
        angle -= math.copysign(180.0, angle)
    rx, ry = _rotate(0.0, DIM_TEXT_GAP_MM, angle)
    mid = ((d1[0] + d2[0]) / 2 + rx, (d1[1] + d2[1]) / 2 + ry)
    declared = geometry.get("declared_value_m")
    text = dimension_text(float(declared) if declared is not None else measured)
    _text(canvas, text, pt(*mid), DIM_TEXT_MM, angle, True)


def _draw_symbol(canvas: Canvas, entity: Mapping[str, Any], context: PdfContext) -> None:
    geometry = entity["geometry"]
    symbol = find_symbol(context.catalog, geometry["symbol_id"])
    _set_style(canvas, entity, context)
    factor = symbol_factor_mm(symbol, float(geometry.get("scale", 1.0)), context.transform)
    at = context.pt(*_xy(geometry["anchor_m"]))
    rotation = float(geometry.get("rotation_deg", 0.0))
    draw_symbol(canvas, symbol, at, rotation, factor, geometry.get("attributes") or {})


def draw_symbol(
    canvas: Canvas,
    symbol: Symbol,
    anchor_pt: Point,
    rotation_deg: float,
    factor_mm: float,
    attributes: Mapping[str, Any],
) -> None:
    """The ``dxf_block`` primitives of ``symbol``, ``factor_mm`` paper mm per block unit.

    Points are transformed here (not with the canvas matrix) so line widths stay unscaled.
    ``ATTDEF``s print the attribute of the same name (lower case), if it has a value.
    """
    unit = mm_to_pt(factor_mm)
    bx, by = symbol.dxf_block.base_point

    def pt(point: Sequence[float]) -> Point:
        x, y = _rotate((point[0] - bx) * unit, (point[1] - by) * unit, rotation_deg)
        return (anchor_pt[0] + x, anchor_pt[1] + y)

    for item in symbol.dxf_block.entities:
        if isinstance(item, DxfLine):
            _stroke(canvas, [pt(item.start), pt(item.end)])
        elif isinstance(item, DxfPolyline):
            _stroke(canvas, [pt(p) for p in item.points], item.closed)
        elif isinstance(item, DxfCircle):
            canvas.circle(*pt(item.center), item.radius * unit, stroke=1, fill=0)
        elif isinstance(item, DxfArc):
            (cx, cy), radius = pt(item.center), item.radius * unit
            extent = (item.end_angle - item.start_angle) % 360.0 or 360.0
            path = canvas.beginPath()
            start = item.start_angle + rotation_deg
            # The ReportLab stubs say int, but any number is accepted.
            path.arc(cx - radius, cy - radius, cx + radius, cy + radius, start, extent)  # pyright: ignore[reportArgumentType]
            canvas.drawPath(path, stroke=1, fill=0)
        else:  # DxfAttributeDefinition
            value = attribute_text(attributes.get(item.tag.lower()))
            if value:
                _text(canvas, value, pt(item.insert), item.height * factor_mm, rotation_deg, False)


ENTITY_DRAWERS: dict[str, EntityDrawer] = {
    "line": _draw_line,
    "polyline": _draw_polyline,
    "point": _draw_point,
    "text": _draw_text,
    "dimension": _draw_dimension,
    "symbol": _draw_symbol,
}


def draw_plan(
    canvas: Canvas, snapshot: Mapping[str, Any], context: PdfContext, layout: SheetLayout
) -> None:
    """Every snapshot entity, in snapshot order, clipped to the drawing area."""
    area = layout.drawing_area
    canvas.saveState()
    clip = canvas.beginPath()
    clip.rect(*(mm_to_pt(v) for v in (area.x, area.y, area.width, area.height)))
    canvas.clipPath(clip, stroke=0, fill=0)
    for entity in snapshot["entities"]:
        if entity["layer"] not in context.layer_colors:
            raise PdfExportError(
                f"entity {entity['id']}: layer {entity['layer']!r} is not in catalog "
                f"{context.catalog.version!r}"
            )
        drawer = ENTITY_DRAWERS.get(entity["type"])
        if drawer is None:
            raise PdfExportError(f"entity {entity['id']}: unknown type {entity['type']!r}")
        try:
            drawer(canvas, entity, context)
        except UnknownSymbolError as error:
            raise PdfExportError(f"entity {entity['id']}: {error}") from error
    canvas.restoreState()


def export_pdf(
    snapshot: dict[str, Any],
    catalog: CatalogDefinition,
    *,
    case_meta: CaseMeta,
    draft: bool,
    generator_version: str,
    frozen_at: datetime | None,
    sheet: SheetChoice = "auto",
    scale: int | None = None,
    dimension_tolerances: DimensionTolerances | None = None,
) -> bytes:
    """PDF bytes of ``snapshot`` on one landscape sheet at a standard scale.

    ``sheet`` and ``scale`` default to the proposal (:func:`plan_page`). ``frozen_at`` is
    ``revisions.frozen_at`` (timezone-aware) or ``None`` for a draft of a working revision.
    ``draft`` does not change the layout yet (the watermark is task 089). Raises
    :class:`PdfExportError` for another catalog version, an unknown layer, type or symbol,
    a degenerate dimension or invalid options, and :class:`ValueError` for a naive
    ``frozen_at``.
    """
    if snapshot["catalog_version"] != catalog.version:
        raise PdfExportError(
            f"snapshot uses catalog {snapshot['catalog_version']!r}, got {catalog.version!r}"
        )
    page = plan_page(snapshot, catalog, sheet=sheet, scale=scale)
    if not page.proposal.fits:
        logger.warning(
            "Plan %s does not fit on %s at 1:%d; the drawing is clipped",
            snapshot["plan_id"],
            page.layout.sheet.name,
            page.proposal.scale,
        )
    sha256 = snapshot_sha256(snapshot)
    revision = int(snapshot["revision_number"])
    context = PdfContext(
        catalog,
        page.transform,
        {layer.name: aci_print_color(layer.aci_color) for layer in catalog.layers},
        tuple(snapshot.get("evidence_table") or ()),
        dimension_tolerances or DimensionTolerances(),
    )
    buffer = io.BytesIO()
    canvas = new_canvas(
        buffer,
        page.layout,
        title=f"Plano {case_meta.case_code} - revisión {revision}",
        subject=f"plan_id={snapshot['plan_id']}; revision_number={revision}; "
        f"snapshot_sha256={sha256}",
        creator=f"Sketch2CAD {generator_version}",
        frozen_at=frozen_at,
        id_seed=f"{snapshot['plan_id']}:{sha256}:{draft}",
    )
    draw_plan(canvas, snapshot, context, page.layout)
    draw_frame(canvas, page.layout)
    rows = title_block_rows(
        case_meta,
        revision_number=revision,
        status=str(snapshot["metric_status"]),
        sha256=sha256,
        scale=page.proposal.scale,
        fits=page.proposal.fits,
    )
    draw_title_block(canvas, page.layout, rows)
    canvas.showPage()
    canvas.save()
    logger.info(
        "Built PDF of plan %s revision %s (%s 1:%d, draft=%s)",
        snapshot["plan_id"],
        revision,
        page.layout.sheet.name,
        page.proposal.scale,
        draft,
    )
    return buffer.getvalue()
