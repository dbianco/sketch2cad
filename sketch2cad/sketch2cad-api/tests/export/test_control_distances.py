"""Control distances agree across model, DXF and PDF within 0.5 % (CA-07, H4-3).

``tests/fixtures/control_plan.json`` holds a horizontal, a vertical and a diagonal control
segment on ``L_CONTROL`` among other geometry. Each length is computed three ways:

* model: from the snapshot coordinates (metres);
* DXF: from the ``LINE`` / ``LWPOLYLINE`` traced back by its ``entity_id`` XDATA, in the
  document units (metres, ``$INSUNITS`` 6);
* PDF: from the vector path whose end points fall on the expected sheet position of the
  segment (the export's world-to-sheet mapping, used only to locate it); its length in
  points is converted to paper millimetres and then to metres with the scale printed on
  the sheet.

The sheet also holds the frame, title block, scale bar, a dimension parallel to the
horizontal segment and a control point dot: matching both end points (and the
``L_CONTROL`` print colour) picks exactly one path per segment.
"""

# pypdfium2 and ezdxf ship without complete type information.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import io
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import pytest
from dxf_snapshots import FROZEN_AT, entity, snapshot
from ezdxf.filemanagement import read as read_drawing
from test_pdf_layout import META, path_vertices, stroke_rgb

from sketch2cad.catalog import default_catalog
from sketch2cad.export import export_dxf
from sketch2cad.export.dxf import APP_ID, INSUNITS_METERS
from sketch2cad.export.pdf import ACI_PRINT_COLORS, SheetChoice, export_pdf, plan_page
from sketch2cad.export.pdf_sheet import PT_PER_MM, SHEETS

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "control_plan.json"
VERSION = "0.1.0"
CONTROL_LAYER = "L_CONTROL"
RELATIVE_TOLERANCE = 0.005  # CA-07: 0.5 %
ENDPOINT_TOLERANCE_PT = 0.1 * PT_PER_MM  # a path must start and end within 0.1 mm
# (sheet, scale, draft) of every export compared; all fit on their sheet.
VARIANTS: list[tuple[SheetChoice, int, bool]] = [
    ("A3", 100, False),
    ("A3", 100, True),
    ("A4", 100, False),
    ("A4", 100, True),
    ("A3", 50, False),
    ("A3", 200, False),
    ("A4", 200, True),
]

Point = tuple[float, float]
Lengths = Mapping[str, float]


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def control_snapshot() -> dict[str, Any]:
    fixture = load_fixture()
    entities = [
        entity(item["number"], item["type"], item["layer"], item["geometry"])
        for item in fixture["entities"]
    ]
    return snapshot(entities, paper_scale=fixture["paper_scale"])


def control_entities(snap: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Snapshot entity of each control segment, by segment name."""
    by_id = {item["id"]: item for item in snap["entities"]}
    found: dict[str, Mapping[str, Any]] = {}
    for segment in load_fixture()["control_segments"]:
        item = by_id[entity(segment["entity"], "line", CONTROL_LAYER, {})["id"]]
        assert item["layer"] == CONTROL_LAYER
        found[segment["name"]] = item
    return found


def polyline_length(points: Sequence[Sequence[float]]) -> float:
    return sum(math.dist(a[:2], b[:2]) for a, b in zip(points, points[1:], strict=False))


def relative_errors(reference: Lengths, measured: Lengths) -> dict[str, float]:
    """``|measured - reference| / reference`` per segment name."""
    assert measured.keys() == reference.keys()
    return {name: abs(measured[name] - ref) / ref for name, ref in reference.items()}


def mismatches(
    reference: Lengths, measured: Lengths, tolerance: float = RELATIVE_TOLERANCE
) -> dict[str, float]:
    """Segments whose relative error exceeds ``tolerance`` (empty when all agree)."""
    errors = relative_errors(reference, measured)
    return {name: error for name, error in errors.items() if error > tolerance}


def assert_agree(model: Lengths, dxf: Lengths, pdf: Lengths) -> None:
    assert mismatches(model, dxf) == {}, f"DXF vs model: {relative_errors(model, dxf)}"
    assert mismatches(model, pdf) == {}, f"PDF vs model: {relative_errors(model, pdf)}"
    assert mismatches(dxf, pdf) == {}, f"PDF vs DXF: {relative_errors(dxf, pdf)}"


def model_lengths(snap: Mapping[str, Any]) -> dict[str, float]:
    return {
        name: polyline_length(item["geometry"]["coords_m"])
        for name, item in control_entities(snap).items()
    }


def dxf_lengths(data: bytes, snap: Mapping[str, Any]) -> dict[str, float]:
    """Length in drawing units (metres) of the DXF entity of each control segment."""
    doc = read_drawing(io.StringIO(data.decode("utf-8")))
    assert doc.header["$INSUNITS"] == INSUNITS_METERS
    by_id: dict[str, Any] = {}
    for graphic in doc.modelspace().query("LINE LWPOLYLINE"):
        if graphic.dxf.layer == CONTROL_LAYER and graphic.has_xdata(APP_ID):
            pairs = [value for code, value in graphic.get_xdata(APP_ID) if code == 1000]
            by_id[dict(pair.split("=", 1) for pair in pairs)["entity_id"]] = graphic
    lengths: dict[str, float] = {}
    for name, item in control_entities(snap).items():
        graphic = by_id[item["id"]]
        if graphic.dxftype() == "LINE":
            points = [graphic.dxf.start, graphic.dxf.end]
        else:
            points = list(graphic.vertices())
        lengths[name] = polyline_length([(p[0], p[1]) for p in points])
    return lengths


def export_both(
    snap: dict[str, Any], *, sheet: SheetChoice, scale: int, draft: bool
) -> tuple[bytes, bytes]:
    """DXF and PDF of ``snap``; drafts carry no freeze time."""
    catalog = default_catalog()
    frozen_at = None if draft else FROZEN_AT
    dxf = export_dxf(snap, catalog, draft=draft, generator_version=VERSION, frozen_at=frozen_at)
    pdf = export_pdf(
        snap,
        catalog,
        case_meta=META,
        draft=draft,
        generator_version=VERSION,
        frozen_at=frozen_at,
        sheet=sheet,
        scale=scale,
    )
    return dxf, pdf


def control_rgb() -> tuple[int, int, int]:
    aci = next(item.aci_color for item in default_catalog().layers if item.name == CONTROL_LAYER)
    color = ACI_PRINT_COLORS[aci].lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def control_paths_pt(
    data: bytes, snap: dict[str, Any], *, sheet: SheetChoice, scale: int
) -> dict[str, list[Point]]:
    """The single PDF path (vertices in points) drawn for each control segment.

    A path belongs to a segment when it is stroked in the ``L_CONTROL`` print colour and
    its first and last vertices lie within :data:`ENDPOINT_TOLERANCE_PT` of the segment's
    end points mapped by the export's world-to-sheet transform.
    """
    transform = plan_page(snap, default_catalog(), sheet=sheet, scale=scale).transform

    def to_pt(xy: Sequence[float]) -> Point:
        x_mm, y_mm = transform.point_mm(float(xy[0]), float(xy[1]))
        return (x_mm * PT_PER_MM, y_mm * PT_PER_MM)

    page = pdfium.PdfDocument(data)[0]
    rgb = control_rgb()
    paths = [
        path_vertices(obj)
        for obj in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_PATH])
        if stroke_rgb(obj) == rgb
    ]
    found: dict[str, list[Point]] = {}
    for name, item in control_entities(snap).items():
        coords = item["geometry"]["coords_m"]
        start, end = to_pt(coords[0]), to_pt(coords[-1])
        matches = [
            path
            for path in paths
            if len(path) == len(coords)
            and math.dist(path[0], start) <= ENDPOINT_TOLERANCE_PT
            and math.dist(path[-1], end) <= ENDPOINT_TOLERANCE_PT
        ]
        assert len(matches) == 1, f"{name}: {len(matches)} PDF paths match its end points"
        found[name] = matches[0]
    return found


def pdf_lengths_m(paths_pt: Mapping[str, Sequence[Point]], assumed_scale: int) -> dict[str, float]:
    """Path lengths read at ``1:assumed_scale``: points -> paper mm -> metres."""
    return {
        name: polyline_length(path) / PT_PER_MM * assumed_scale / 1000.0
        for name, path in paths_pt.items()
    }


def measure_pdf(
    data: bytes, snap: dict[str, Any], *, sheet: SheetChoice, scale: int
) -> dict[str, float]:
    """Control lengths in metres of a PDF requested at ``sheet`` and ``1:scale``.

    The sheet size and the printed scale are checked first, so the conversion uses the
    scale a reader of the sheet would use.
    """
    page = pdfium.PdfDocument(data)[0]
    assert page.get_size()[0] == pytest.approx(SHEETS[sheet].width_mm * PT_PER_MM, abs=1e-3)
    text = page.get_textpage().get_text_range()
    assert f"1:{scale}" in text and "no cabe" not in text
    return pdf_lengths_m(control_paths_pt(data, snap, sheet=sheet, scale=scale), scale)


def test_fixture_has_horizontal_vertical_and_diagonal_control_segments() -> None:
    snap = control_snapshot()
    segments = control_entities(snap)
    assert list(segments) == ["horizontal", "vertical", "diagonal"]
    (hx0, hy0), (hx1, hy1) = segments["horizontal"]["geometry"]["coords_m"]
    (vx0, vy0), (vx1, vy1) = segments["vertical"]["geometry"]["coords_m"]
    (dx0, dy0), (dx1, dy1) = segments["diagonal"]["geometry"]["coords_m"]
    assert hy0 == hy1 and hx0 != hx1
    assert vx0 == vx1 and vy0 != vy1
    assert dx0 != dx1 and dy0 != dy1
    expected = {item["name"]: item["expected_m"] for item in load_fixture()["control_segments"]}
    assert expected == {"horizontal": 12.0, "vertical": 7.5, "diagonal": 10.0}
    assert model_lengths(snap) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize(("sheet", "scale", "draft"), VARIANTS)
def test_control_distances_agree_across_model_dxf_and_pdf(
    sheet: SheetChoice, scale: int, draft: bool
) -> None:
    snap = control_snapshot()
    dxf_data, pdf_data = export_both(snap, sheet=sheet, scale=scale, draft=draft)
    model = model_lengths(snap)
    dxf = dxf_lengths(dxf_data, snap)
    pdf = measure_pdf(pdf_data, snap, sheet=sheet, scale=scale)
    assert_agree(model, dxf, pdf)


def test_draft_final_a4_and_a3_exports_measure_the_same_distances() -> None:
    snap = control_snapshot()
    measured: dict[tuple[SheetChoice, int, bool], dict[str, float]] = {}
    for sheet, scale, draft in VARIANTS:
        dxf_data, pdf_data = export_both(snap, sheet=sheet, scale=scale, draft=draft)
        measured[(sheet, scale, draft)] = measure_pdf(pdf_data, snap, sheet=sheet, scale=scale)
        assert dxf_lengths(dxf_data, snap) == pytest.approx(model_lengths(snap), abs=1e-9)
    reference = measured[("A3", 100, False)]
    for variant, lengths in measured.items():
        assert mismatches(reference, lengths) == {}, f"{variant}: {lengths}"


@pytest.mark.parametrize(
    ("measured", "agrees"),
    [
        ({"a": 12.0, "b": 7.5}, True),
        ({"a": 12.0 * 1.0049, "b": 7.5 * 0.9951}, True),
        ({"a": 12.0 * 1.0051, "b": 7.5}, False),
        ({"a": 12.0, "b": 7.5 * 0.9949}, False),
    ],
)
def test_comparison_reports_errors_above_half_a_percent(
    measured: dict[str, float], agrees: bool
) -> None:
    assert (mismatches({"a": 12.0, "b": 7.5}, measured) == {}) is agrees


@pytest.mark.parametrize(
    ("drawn_scale", "assumed_scale", "sheet"),
    [(200, 100, "A3"), (200, 100, "A4"), (100, 200, "A3"), (50, 100, "A3")],
)
def test_pdf_read_at_the_wrong_scale_is_reported_as_a_mismatch(
    drawn_scale: int, assumed_scale: int, sheet: SheetChoice
) -> None:
    """Negative control: the comparison catches a sheet read at another scale."""
    snap = control_snapshot()
    _, pdf_data = export_both(snap, sheet=sheet, scale=drawn_scale, draft=False)
    paths = control_paths_pt(pdf_data, snap, sheet=sheet, scale=drawn_scale)
    model = model_lengths(snap)
    assert mismatches(model, pdf_lengths_m(paths, drawn_scale)) == {}
    wrong = mismatches(model, pdf_lengths_m(paths, assumed_scale))
    assert set(wrong) == set(model)
    expected_error = abs(assumed_scale / drawn_scale - 1.0)
    assert wrong == pytest.approx(dict.fromkeys(model, expected_error), rel=RELATIVE_TOLERANCE)
