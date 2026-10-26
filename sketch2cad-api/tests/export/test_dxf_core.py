"""DXF export core: header, catalog layers, lines, polylines, points, metadata (H4-1, CA-06)."""

# ezdxf leaves most attributes unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

import io
import re
from collections import Counter
from typing import Any

import pytest
from dxf_snapshots import default_entities, entity, snapshot
from ezdxf import units
from ezdxf.document import Drawing
from ezdxf.filemanagement import read as read_drawing

from sketch2cad.catalog import default_catalog
from sketch2cad.export import DxfExportError, export_dxf
from sketch2cad.export.dxf import APP_ID, DRAFT_WATERMARK, SHEET_LAYER, paper_scale
from sketch2cad.export.dxf_layers import allowed_layer_names
from sketch2cad.revisions.snapshot import snapshot_sha256

TOLERANCE_M = 1e-6
VERSION = "0.1.0"
EXPORTED_TYPES = {"line": "LINE", "polyline": "LWPOLYLINE", "point": "POINT"}
# Values ezdxf takes from the clock or uuid4 on every write (D-3); 086 pins them.
CLOCK_HEADER_VARS = ("$TDCREATE", "$TDUPDATE", "$FINGERPRINTGUID", "$VERSIONGUID")
EZDXF_MARKER = re.compile(r"^\d+\.\d+\.\d+ @ ")


@pytest.fixture
def fixture_snapshot() -> dict[str, Any]:
    return snapshot(default_entities())


def export(snap: dict[str, Any], *, draft: bool = False) -> bytes:
    return export_dxf(snap, default_catalog(), draft=draft, generator_version=VERSION)


def read(data: bytes) -> Drawing:
    return read_drawing(io.StringIO(data.decode("utf-8")))


def xdata(graphic: Any) -> dict[str, str]:
    """The ``key=value`` strings of the :data:`APP_ID` XDATA of ``graphic``."""
    pairs = [value for code, value in graphic.get_xdata(APP_ID) if code == 1000]
    return dict(pair.split("=", 1) for pair in pairs)


def tagged(doc: Drawing) -> dict[str, Any]:
    """Model space entities that carry an ``entity_id``, by id."""
    found: dict[str, Any] = {}
    for graphic in doc.modelspace():
        if graphic.has_xdata(APP_ID) and "entity_id" in xdata(graphic):
            found[xdata(graphic)["entity_id"]] = graphic
    return found


def masked(data: bytes) -> list[str]:
    """DXF lines with the clock and uuid4 values replaced by a placeholder."""
    lines = data.decode("utf-8").split("\n")
    for index, line in enumerate(lines):
        if line in CLOCK_HEADER_VARS:
            lines[index + 2] = "<masked>"
        elif EZDXF_MARKER.match(line):
            lines[index] = "<masked>"
    return lines


def section(data: bytes, name: str) -> str:
    text = data.decode("utf-8")
    start = text.index(f"\n  2\n{name}\n")
    return text[start : text.index("\n  0\nENDSEC\n", start)]


def test_entities_per_layer_and_coordinates_round_trip(fixture_snapshot: dict[str, Any]) -> None:
    doc = read(export(fixture_snapshot))
    expected = Counter(
        (e["layer"], EXPORTED_TYPES[e["type"]])
        for e in fixture_snapshot["entities"]
        if e["type"] in EXPORTED_TYPES
    )
    found = tagged(doc)
    assert Counter((g.dxf.layer, g.dxftype()) for g in found.values()) == expected
    for item in fixture_snapshot["entities"]:
        if item["type"] not in EXPORTED_TYPES:
            assert item["id"] not in found
            continue
        graphic = found[item["id"]]
        if graphic.dxftype() == "LINE":
            points = [graphic.dxf.start, graphic.dxf.end]
        elif graphic.dxftype() == "POINT":
            points = [graphic.dxf.location]
        else:
            points = list(graphic.vertices())
            assert graphic.closed == item["geometry"]["closed"]
        coords = item["geometry"]["coords_m"]
        assert len(points) == len(coords)
        for point, (x, y) in zip(points, coords, strict=True):
            assert abs(point[0] - x) < TOLERANCE_M
            assert abs(point[1] - y) < TOLERANCE_M


def test_reread_document_audits_clean(fixture_snapshot: dict[str, Any]) -> None:
    for draft in (False, True):
        auditor = read(export(fixture_snapshot, draft=draft)).audit()
        assert len(auditor.errors) == 0
        assert len(auditor.fixes) == 0


def test_header_is_r2013_in_metres(fixture_snapshot: dict[str, Any]) -> None:
    doc = read(export(fixture_snapshot))
    assert doc.dxfversion == "AC1027"
    assert doc.header["$INSUNITS"] == 6
    assert doc.header["$MEASUREMENT"] == 1
    assert doc.units == units.M


def test_layers_come_from_the_catalog_only(fixture_snapshot: dict[str, Any]) -> None:
    catalog = default_catalog()
    doc = read(export(fixture_snapshot, draft=True))
    names = {layer.dxf.name for layer in doc.layers}
    assert names <= allowed_layer_names(catalog)
    for layer in catalog.layers:
        assert doc.layers.get(layer.name).color == layer.aci_color


def test_document_metadata_in_xdata(fixture_snapshot: dict[str, Any]) -> None:
    doc = read(export(fixture_snapshot))
    carriers = [
        g for g in doc.modelspace().query(f"POINT[layer=='{SHEET_LAYER}']") if g.has_xdata(APP_ID)
    ]
    assert len(carriers) == 1
    assert carriers[0].dxf.invisible == 1
    assert xdata(carriers[0]) == {
        "plan_id": fixture_snapshot["plan_id"],
        "revision_number": "2",
        "snapshot_sha256": snapshot_sha256(fixture_snapshot),
        "metric_status": "metric",
        "generator_version": VERSION,
    }
    assert (1071, 2) in list(carriers[0].get_xdata(APP_ID))


def test_draft_watermark_only_in_draft(fixture_snapshot: dict[str, Any]) -> None:
    final = read(export(fixture_snapshot, draft=False))
    draft = read(export(fixture_snapshot, draft=True))
    assert [t.dxf.text for t in final.modelspace().query("TEXT")] == []
    (watermark,) = draft.modelspace().query("TEXT")
    assert watermark.dxf.text == DRAFT_WATERMARK
    assert watermark.dxf.layer == SHEET_LAYER
    assert watermark.dxf.height == pytest.approx(10.0 * 100 / 1000)
    assert (watermark.dxf.insert.x, watermark.dxf.insert.y) == pytest.approx((-1.5, -2.0))


def test_export_is_deterministic_apart_from_clock_values(
    fixture_snapshot: dict[str, Any],
) -> None:
    first = export(fixture_snapshot, draft=True)
    second = export(fixture_snapshot, draft=True)
    for name in ("TABLES", "BLOCKS", "ENTITIES"):
        assert section(first, name) == section(second, name)
    assert masked(first) == masked(second)


def test_unknown_layer_is_rejected() -> None:
    bad = entity(9, "line", "L_WALLS", {"coords_m": [[0.0, 0.0], [1.0, 0.0]]})
    with pytest.raises(DxfExportError, match="L_WALLS"):
        export(snapshot([*default_entities(), bad]))


def test_unknown_entity_type_is_rejected() -> None:
    bad = entity(9, "spline", "L_STRUCTURE", {"coords_m": [[0.0, 0.0], [1.0, 0.0]]})
    with pytest.raises(DxfExportError, match="spline"):
        export(snapshot([bad]))


def test_other_catalog_version_is_rejected(fixture_snapshot: dict[str, Any]) -> None:
    with pytest.raises(DxfExportError, match="catalog"):
        export({**fixture_snapshot, "catalog_version": "default-2"})


def test_empty_snapshot_exports_metadata_only() -> None:
    doc = read(export(snapshot([]), draft=True))
    assert tagged(doc) == {}
    (watermark,) = doc.modelspace().query("TEXT")
    assert (watermark.dxf.insert.x, watermark.dxf.insert.y) == (0.0, 0.0)
    assert len(doc.audit().errors) == 0


def test_paper_scale_falls_back_to_provisional_scale() -> None:
    assert paper_scale(snapshot([], paper_scale=250)) == 250
    assert paper_scale(snapshot([], paper_scale=None)) == 100
    assert paper_scale({**snapshot([]), "calibration": None}) == 100
