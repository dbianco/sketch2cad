"""DXF export of texts, symbols with ATTRIBs and evidence markers (H4-2, REQ-12)."""

# ezdxf leaves most attributes unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

import io
import uuid
from collections import Counter
from typing import Any

import pytest
from dxf_snapshots import FROZEN_AT, default_entities, entity, snapshot
from ezdxf.document import Drawing
from ezdxf.filemanagement import read as read_drawing

from sketch2cad.catalog import default_catalog
from sketch2cad.db.models import Entity, Evidence, MetricStatus, Plan, Revision, SourceMode
from sketch2cad.export import DxfExportError, export_dxf
from sketch2cad.export.dxf import APP_ID
from sketch2cad.revisions.snapshot import build_snapshot

VERSION = "0.1.0"
EVIDENCE_ID = "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3"
SYMBOL_IDS = [symbol.symbol_id for symbol in default_catalog().symbols]


def symbol(
    number: int,
    symbol_id: str,
    layer: str,
    *,
    anchor: tuple[float, float] = (1.0, 2.0),
    rotation_deg: float = 0.0,
    scale: float = 1.0,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return entity(
        number,
        "symbol",
        layer,
        {
            "anchor_m": list(anchor),
            "rotation_deg": rotation_deg,
            "scale": scale,
            "symbol_id": symbol_id,
            "attributes": attributes or {},
        },
    )


def text(number: int, content: str, *, height_mm: float = 2.5, rotation: float = 0.0) -> Any:
    return entity(
        number,
        "text",
        "L_TEXT",
        {
            "anchor_m": [3.5, -1.25],
            "rotation_deg": rotation,
            "height_mm_paper": height_mm,
            "content": content,
        },
    )


def evidence_point(number: int, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
    point = entity(number, "point", "L_EVIDENCE", {"coords_m": [[2.5, 1.5]]})
    return {**point, "evidence_id": EVIDENCE_ID, "attributes": attributes or {}}


def export_read(snap: dict[str, Any], *, draft: bool = False) -> Drawing:
    data = export_dxf(
        snap, default_catalog(), draft=draft, generator_version=VERSION, frozen_at=FROZEN_AT
    )
    return read_drawing(io.StringIO(data.decode("utf-8")))


def entity_id(graphic: Any) -> str | None:
    if not graphic.has_xdata(APP_ID):
        return None
    for code, value in graphic.get_xdata(APP_ID):
        if code == 1000 and value.startswith("entity_id="):
            return value.split("=", 1)[1]
    return None


def inserts(doc: Drawing, block: str) -> list[Any]:
    return list(doc.modelspace().query(f"INSERT[name=='{block}']"))


def test_evidence_number_is_an_attrib_of_the_marker_insert() -> None:
    marker = symbol(1, "evidence_marker", "L_EVIDENCE", attributes={"evidence_number": 3})
    doc = export_read(snapshot([marker]))
    (insert,) = inserts(doc, "S2C_evidence_marker")
    assert insert.get_attrib_text("EVIDENCE_NUMBER") == "3"
    assert insert.get_attrib_text("LABEL") == ""
    assert {attrib.dxf.layer for attrib in insert.attribs} == {"L_EVIDENCE"}
    assert entity_id(insert) == marker["id"]


@pytest.mark.parametrize(
    ("paper_scale", "entity_scale", "expected"),
    [(100, 1.0, 0.6), (50, 1.0, 0.3), (100, 2.0, 1.2), (200, 0.5, 0.6)],
)
def test_paper_mm_symbol_scale_follows_paper_scale(
    paper_scale: int, entity_scale: float, expected: float
) -> None:
    marker = symbol(1, "evidence_marker", "L_EVIDENCE", scale=entity_scale)
    (insert,) = inserts(export_read(snapshot([marker], paper_scale)), "S2C_evidence_marker")
    assert insert.dxf.xscale == pytest.approx(expected)
    assert insert.dxf.yscale == pytest.approx(expected)


@pytest.mark.parametrize(("entity_scale", "expected"), [(1.0, 0.9), (2.0, 1.8)])
@pytest.mark.parametrize("paper_scale", [50, 100])
def test_world_m_symbol_scale_ignores_paper_scale(
    paper_scale: int, entity_scale: float, expected: float
) -> None:
    door = symbol(1, "door", "L_ACCESS", scale=entity_scale)
    (insert,) = inserts(export_read(snapshot([door], paper_scale)), "S2C_door")
    assert insert.dxf.xscale == pytest.approx(expected)
    assert insert.dxf.yscale == pytest.approx(expected)


def test_anchor_rotation_and_entity_layer_are_preserved() -> None:
    tree = symbol(1, "tree", "L_ACCESS", anchor=(-3.25, 7.125), rotation_deg=37.5)
    (insert,) = inserts(export_read(snapshot([tree])), "S2C_tree")
    assert (insert.dxf.insert.x, insert.dxf.insert.y) == pytest.approx((-3.25, 7.125))
    assert insert.dxf.rotation == pytest.approx(37.5)
    assert insert.dxf.layer == "L_ACCESS"


def test_each_used_symbol_has_one_block() -> None:
    entities = [
        symbol(1, "evidence_marker", "L_EVIDENCE", attributes={"evidence_number": 1}),
        symbol(2, "evidence_marker", "L_EVIDENCE", attributes={"evidence_number": 2}),
        symbol(3, "evidence_marker", "L_EVIDENCE", attributes={"evidence_number": 3}),
        symbol(4, "door", "L_ACCESS"),
        symbol(5, "door", "L_ACCESS"),
        evidence_point(6, {"evidence_number": 4}),
    ]
    doc = export_read(snapshot(entities))
    names = [block.name for block in doc.blocks if block.name.startswith("S2C_")]
    assert sorted(names) == ["S2C_door", "S2C_evidence_marker"]
    assert len(inserts(doc, "S2C_evidence_marker")) == 4
    assert len(inserts(doc, "S2C_door")) == 2
    numbers = [i.get_attrib_text("EVIDENCE_NUMBER") for i in inserts(doc, "S2C_evidence_marker")]
    assert numbers == ["1", "2", "3", "4"]


def test_text_height_style_and_unicode_round_trip() -> None:
    content = "Habitación Ñandú — señal ⚠ 🚗"
    doc = export_read(snapshot([text(1, content, rotation=90.0)]))
    (written,) = doc.modelspace().query("TEXT[layer=='L_TEXT']")
    assert written.dxf.text == content
    assert written.dxf.height == pytest.approx(0.25)
    assert written.dxf.rotation == pytest.approx(90.0)
    assert written.dxf.style == "S2C_TEXT"
    assert (written.dxf.insert.x, written.dxf.insert.y) == pytest.approx((3.5, -1.25))
    assert "S2C_TEXT" in doc.styles


def test_text_height_at_another_paper_scale() -> None:
    doc = export_read(snapshot([text(1, "N", height_mm=3.5)], paper_scale=50))
    (written,) = doc.modelspace().query("TEXT[layer=='L_TEXT']")
    assert written.dxf.height == pytest.approx(0.175)


def test_evidence_point_adds_a_marker_insert() -> None:
    doc = export_read(snapshot([evidence_point(1, {"evidence_number": 7})]))
    graphics = [g for g in doc.modelspace() if entity_id(g) is not None]
    assert sorted(g.dxftype() for g in graphics) == ["INSERT", "POINT"]
    (insert,) = inserts(doc, "S2C_evidence_marker")
    assert insert.get_attrib_text("EVIDENCE_NUMBER") == "7"
    assert (insert.dxf.insert.x, insert.dxf.insert.y) == pytest.approx((2.5, 1.5))
    assert insert.dxf.xscale == pytest.approx(0.6)


def test_evidence_table_number_wins_over_entity_attributes() -> None:
    point = evidence_point(1, {"evidence_number": 7})
    row = {
        "evidence_id": EVIDENCE_ID,
        "evidence_number": 12,
        "entity_id": point["id"],
        "label": "Casquillo",
        "x_m": 2.5,
        "y_m": 1.5,
        "metric_origin": "scaled_drawing",
    }
    doc = export_read({**snapshot([point]), "evidence_table": [row]})
    (insert,) = inserts(doc, "S2C_evidence_marker")
    assert insert.get_attrib_text("EVIDENCE_NUMBER") == "12"
    assert insert.get_attrib_text("LABEL") == "Casquillo"


def test_frozen_evidence_table_numbers_the_marker() -> None:
    found = Evidence(id=uuid.UUID(EVIDENCE_ID), number=4, label="Huella")
    point = Entity(
        id=uuid.UUID(int=1),
        type="point",
        layer="L_EVIDENCE",
        geometry={"kind": "point", "coords_m": [[2.5, 1.5]]},
        attributes={},
        evidence_id=found.id,
        source={"method": "manual"},
        review_status="accepted",
        metric_origin="scaled_drawing",
    )
    plan = Plan(
        id=uuid.UUID(int=7),
        source_mode=SourceMode.SCALED_DRAWING,
        metric_status=MetricStatus.METRIC,
    )
    snap = build_snapshot(plan, Revision(number=1), None, "default-1", [point], [found])
    (insert,) = inserts(export_read(snap), "S2C_evidence_marker")
    assert insert.get_attrib_text("EVIDENCE_NUMBER") == "4"
    assert insert.get_attrib_text("LABEL") == "Huella"


def test_plain_point_has_no_marker() -> None:
    doc = export_read(snapshot(default_entities()))
    assert inserts(doc, "S2C_evidence_marker") == []
    assert "S2C_evidence_marker" not in doc.blocks


def test_round_trip_counts_per_layer_and_audit() -> None:
    entities = [
        *default_entities(),
        symbol(11, "north_arrow", "L_ORIENTATION"),
        symbol(12, "generic_vehicle", "L_SYMBOLS", attributes={"label": "V1"}),
        symbol(13, "evidence_marker", "L_EVIDENCE", attributes={"evidence_number": 1}),
        evidence_point(14, {"evidence_number": 2}),
        text(15, "Calle Mayor"),
    ]
    dxf_types = {"line": ["LINE"], "polyline": ["LWPOLYLINE"], "text": ["TEXT"]}
    dxf_types |= {"symbol": ["INSERT"], "point": ["POINT"]}
    expected: Counter[tuple[str, str]] = Counter()
    for item in entities:
        markers = ["INSERT"] if item["type"] == "point" and item["evidence_id"] else []
        for dxf_type in [*dxf_types[item["type"]], *markers]:
            expected[(item["layer"], dxf_type)] += 1
    for draft in (False, True):
        doc = export_read(snapshot(entities), draft=draft)
        found = Counter(
            (g.dxf.layer, g.dxftype()) for g in doc.modelspace() if entity_id(g) is not None
        )
        assert found == expected
        auditor = doc.audit()
        assert len(auditor.errors) == 0
        assert len(auditor.fixes) == 0


def test_unknown_symbol_is_rejected() -> None:
    with pytest.raises(DxfExportError, match="hydrant"):
        export_read(snapshot([symbol(1, "hydrant", "L_SYMBOLS")]))


@pytest.mark.parametrize("symbol_id", SYMBOL_IDS)
def test_every_catalog_symbol_exports_and_audits_clean(symbol_id: str) -> None:
    spec = next(s for s in default_catalog().symbols if s.symbol_id == symbol_id)
    values = {a.name: (5 if a.type == "integer" else "X") for a in spec.attributes}
    item = symbol(1, symbol_id, spec.default_layer, attributes=values)
    doc = export_read(snapshot([item]))
    block = doc.blocks.get(spec.dxf_block.name)
    assert len(block) == len(spec.dxf_block.entities)
    (insert,) = inserts(doc, spec.dxf_block.name)
    assert {a.dxf.tag: a.dxf.text for a in insert.attribs} == {
        name.upper(): str(value) for name, value in values.items()
    }
    auditor = doc.audit()
    assert len(auditor.errors) == 0
    assert len(auditor.fixes) == 0
