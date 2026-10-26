"""DXF export of dimensions: aligned DIMENSION, declared text override, alert style (REQ-12)."""

# ezdxf leaves most attributes unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

import io
import math
from collections import Counter
from typing import Any

import pytest
from dxf_snapshots import FROZEN_AT, default_entities, entity, snapshot
from ezdxf.document import Drawing
from ezdxf.filemanagement import read as read_drawing

from sketch2cad.catalog import default_catalog
from sketch2cad.entities.dimensions import DimensionTolerances, measured_length
from sketch2cad.export import DxfExportError, export_dxf
from sketch2cad.export.dxf import APP_ID
from sketch2cad.export.dxf_dimensions import DIM_ALERT_STYLE, DIM_STYLE, dimension_text

VERSION = "0.1.0"
MEASURE_TOLERANCE_M = 1e-9
ANGLE = math.radians(30.0)
REAL_4_24 = (4.24 * math.cos(ANGLE), 4.24 * math.sin(ANGLE))


def dimension(
    number: int,
    p2: tuple[float, float],
    *,
    declared: float | None = None,
    offset: float = 0.5,
    p1: tuple[float, float] = (0.0, 0.0),
    layer: str = "L_DIMENSIONS",
) -> dict[str, Any]:
    return entity(
        number,
        "dimension",
        layer,
        {
            "p1_m": list(p1),
            "p2_m": list(p2),
            "offset_m": offset,
            "declared_value_m": declared,
            "measured_value_m": measured_length(p1, p2),
        },
    )


def export(
    snap: dict[str, Any],
    *,
    draft: bool = False,
    tolerances: DimensionTolerances | None = None,
) -> bytes:
    return export_dxf(
        snap,
        default_catalog(),
        draft=draft,
        generator_version=VERSION,
        frozen_at=FROZEN_AT,
        dimension_tolerances=tolerances,
    )


def read(data: bytes) -> Drawing:
    return read_drawing(io.StringIO(data.decode("utf-8")))


def dimensions(doc: Drawing) -> list[Any]:
    return list(doc.modelspace().query("DIMENSION"))


def only_dimension(item: dict[str, Any], **kwargs: Any) -> Any:
    (found,) = dimensions(read(export(snapshot([item]), **kwargs)))
    return found


def entity_id(graphic: Any) -> str | None:
    if not graphic.has_xdata(APP_ID):
        return None
    for code, value in graphic.get_xdata(APP_ID):
        if code == 1000 and value.startswith("entity_id="):
            return value.split("=", 1)[1]
    return None


def block_texts(doc: Drawing, graphic: Any) -> list[str]:
    """Plain text of the MTEXT/TEXT entities ``render()`` put in the dimension's block."""
    block: list[Any] = list(doc.blocks.get(graphic.dxf.geometry))
    return [g.plain_text() for g in block if g.dxftype() in {"MTEXT", "TEXT"}]


def section(data: bytes, name: str) -> str:
    text = data.decode("utf-8")
    start = text.index(f"\n  2\n{name}\n")
    return text[start : text.index("\n  0\nENDSEC\n", start)]


def test_declared_value_is_the_text_and_real_length_the_measurement() -> None:
    item = dimension(1, REAL_4_24, declared=4.20)
    data = export(snapshot([item]))
    doc = read(data)
    (found,) = dimensions(doc)
    assert found.dxf.text == "4.20"
    assert block_texts(doc, found) == ["4.20"]
    assert found.get_measurement() == pytest.approx(4.24, abs=MEASURE_TOLERANCE_M)
    start, end = found.dxf.defpoint2, found.dxf.defpoint3
    assert math.dist((start.x, start.y), (end.x, end.y)) == pytest.approx(
        4.24, abs=MEASURE_TOLERANCE_M
    )
    assert (end.x, end.y) == pytest.approx(REAL_4_24, abs=MEASURE_TOLERANCE_M)
    assert "4,2" not in section(data, "ENTITIES") + section(data, "BLOCKS")


def test_plain_measurement_shows_the_measured_length() -> None:
    doc = read(export(snapshot([dimension(1, (3.0, 4.0))])))
    (found,) = dimensions(doc)
    assert found.dxf.text == "5.00"
    assert block_texts(doc, found) == ["5.00"]
    assert found.get_measurement() == pytest.approx(5.0, abs=MEASURE_TOLERANCE_M)


def test_text_has_two_decimals_and_a_point() -> None:
    assert dimension_text(4.2) == "4.20"
    assert dimension_text(12.0) == "12.00"
    assert dimension_text(0.456) == "0.46"


def test_discrepant_dimension_uses_the_alert_style() -> None:
    assert only_dimension(dimension(1, REAL_4_24, declared=4.20)).dxf.dimstyle == DIM_ALERT_STYLE
    assert only_dimension(dimension(1, REAL_4_24, declared=4.225)).dxf.dimstyle == DIM_STYLE
    assert only_dimension(dimension(1, REAL_4_24, declared=4.22)).dxf.dimstyle == DIM_STYLE
    assert only_dimension(dimension(1, REAL_4_24)).dxf.dimstyle == DIM_STYLE


def test_tolerance_override_changes_the_style() -> None:
    lenient = DimensionTolerances(discrepancy_tolerance_m=0.05)
    strict = DimensionTolerances(discrepancy_tolerance_m=0.01)
    discrepant = dimension(1, REAL_4_24, declared=4.20)
    close = dimension(1, REAL_4_24, declared=4.225)
    assert only_dimension(discrepant, tolerances=lenient).dxf.dimstyle == DIM_STYLE
    assert only_dimension(close, tolerances=strict).dxf.dimstyle == DIM_ALERT_STYLE


def test_dimension_styles_are_sized_from_the_paper_scale() -> None:
    doc = read(export(snapshot([dimension(1, REAL_4_24)], paper_scale=50)))
    normal, alert = doc.dimstyles.get(DIM_STYLE), doc.dimstyles.get(DIM_ALERT_STYLE)
    for style in (normal, alert):
        assert style.dxf.dimtxsty == "S2C_TEXT"
        assert style.dxf.dimtxt == pytest.approx(2.5 * 50 / 1000)
        assert style.dxf.dimasz == pytest.approx(2.5 * 50 / 1000)
        assert style.dxf.dimlunit == 2
        assert style.dxf.dimdec == 2
        assert style.dxf.dimdsep == ord(".")
        assert style.dxf.dimzin == 0
    assert (normal.dxf.dimclrd, normal.dxf.dimclrt) == (256, 256)
    assert (alert.dxf.dimclrd, alert.dxf.dimclre, alert.dxf.dimclrt) == (1, 1, 1)


def test_reread_dimension_is_aligned_with_entity_id_and_layer() -> None:
    item = dimension(1, REAL_4_24, declared=4.20, layer="L_STRUCTURE")
    found = only_dimension(item)
    assert found.dxftype() == "DIMENSION"
    assert found.dimtype == 1
    assert found.dxf.layer == "L_STRUCTURE"
    assert entity_id(found) == item["id"]


@pytest.mark.parametrize("offset", [0.75, 0.0, -0.75])
def test_offset_places_the_dimension_line_and_audits_clean(offset: float) -> None:
    doc = read(export(snapshot([dimension(1, (4.0, 0.0), offset=offset)])))
    (found,) = dimensions(doc)
    assert found.dxf.defpoint.y == pytest.approx(offset, abs=MEASURE_TOLERANCE_M)
    assert found.get_measurement() == pytest.approx(4.0, abs=MEASURE_TOLERANCE_M)
    auditor = doc.audit()
    assert len(auditor.errors) == 0
    assert len(auditor.fixes) == 0


def mixed_entities() -> list[dict[str, Any]]:
    return [
        *default_entities(),
        dimension(11, REAL_4_24, declared=4.20),
        dimension(12, (4.25, 3.125), p1=(4.25, 0.0), declared=3.125, offset=0.0),
        dimension(13, (0.0, 3.0), offset=-0.4, layer="L_STRUCTURE"),
    ]


def test_dimensions_counted_per_layer_and_audit_clean_after_round_trip() -> None:
    entities = mixed_entities()
    expected = Counter(
        (item["layer"], "DIMENSION") for item in entities if item["type"] == "dimension"
    )
    for draft in (False, True):
        doc = read(export(snapshot(entities), draft=draft))
        found = Counter(
            (g.dxf.layer, g.dxftype())
            for g in doc.modelspace()
            if entity_id(g) is not None and g.dxftype() == "DIMENSION"
        )
        assert found == expected
        auditor = doc.audit()
        assert len(auditor.errors) == 0
        assert len(auditor.fixes) == 0


def test_export_with_dimensions_is_deterministic() -> None:
    snap = snapshot(mixed_entities())
    first, second = export(snap, draft=True), export(snap, draft=True)
    for name in ("TABLES", "BLOCKS", "ENTITIES"):
        assert section(first, name) == section(second, name)
    doc = read(first)
    assert [d.dxf.geometry for d in dimensions(doc)] == ["*D1", "*D2", "*D3"]


def test_dimension_with_coincident_points_is_rejected() -> None:
    with pytest.raises(DxfExportError, match="coincide"):
        export(snapshot([dimension(1, (2.0, 2.0), p1=(2.0, 2.0))]))
