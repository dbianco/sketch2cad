"""Golden DXF ``tests/golden/plan.dxf``: the file the CAD viewer checks open (H4-1, H4-2).

The golden file is the final-mode export of :func:`golden_snapshot`, a revision that uses
every entity kind the exporter writes: lines, open and closed polylines, plain and evidence
points, texts, symbols with ATTRIBs (an evidence marker among them) and dimensions that are
declared, not declared and discrepant, on nine layers. ``docs/dxf-viewers.md`` describes how
to open it in FreeCAD, LibreCAD and QCAD.

The committed bytes must equal a fresh export. If the exporter output changes on purpose,
regenerate the file and the hash from ``sketch2cad-api/`` with
``uv run python scripts/regenerate_golden_dxf.py`` and repeat the viewer checks.
"""

# ezdxf leaves most attributes unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

import hashlib
import io
import math
from collections import Counter
from pathlib import Path
from typing import Any

from dxf_snapshots import FROZEN_AT, entity, mixed_entities, snapshot
from ezdxf.document import Drawing
from ezdxf.filemanagement import read as read_drawing

from sketch2cad.catalog import default_catalog
from sketch2cad.entities.dimensions import measured_length
from sketch2cad.export import export_dxf
from sketch2cad.export.dxf import APP_ID

API_DIR = Path(__file__).resolve().parents[2]
GOLDEN_PATH = API_DIR / "tests" / "golden" / "plan.dxf"
GENERATOR_VERSION = "0.1.0"
EVIDENCE_ID = "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3"
# Length of the L_CONTROL segment: a viewer working in millimetres must measure 5000.
CONTROL_SEGMENT_M = 5.0
# SHA-256 of tests/golden/plan.dxf; scripts/regenerate_golden_dxf.py prints the new value.
GOLDEN_SHA256 = "99d1a45f9e40de0023eb459b8113f16f4ba9ca91caab14646b4f35949abd04b4"
REGENERATE_HINT = (
    "tests/golden/plan.dxf no longer matches the export of the golden snapshot. If the "
    "exporter change is intended, run `uv run python scripts/regenerate_golden_dxf.py` from "
    "sketch2cad-api/, paste the printed SHA-256 into GOLDEN_SHA256 and repeat the viewer "
    "checks of docs/dxf-viewers.md."
)
# Entities written per layer (metadata point and annotation/sheet extras excluded).
EXPECTED_LAYER_COUNTS = {
    "L_STRUCTURE": 3,
    "L_ACCESS": 1,
    "L_EVIDENCE": 5,
    "L_SYMBOLS": 2,
    "L_DIMENSIONS": 4,
    "L_TEXT": 1,
    "L_ORIENTATION": 1,
    "L_ANNOTATIONS": 1,
    "L_CONTROL": 3,
}


def symbol(
    number: int,
    symbol_id: str,
    layer: str,
    anchor: tuple[float, float],
    *,
    rotation_deg: float = 0.0,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return entity(
        number,
        "symbol",
        layer,
        {
            "anchor_m": list(anchor),
            "rotation_deg": rotation_deg,
            "scale": 1.0,
            "symbol_id": symbol_id,
            "attributes": attributes or {},
        },
    )


def dimension(
    number: int, p1: tuple[float, float], p2: tuple[float, float], declared: float | None
) -> dict[str, Any]:
    return entity(
        number,
        "dimension",
        "L_DIMENSIONS",
        {
            "p1_m": list(p1),
            "p2_m": list(p2),
            "offset_m": -0.6,
            "declared_value_m": declared,
            "measured_value_m": measured_length(p1, p2),
        },
    )


def golden_snapshot() -> dict[str, Any]:
    """The revision behind the golden DXF (``mixed_entities`` plus one of each other case)."""
    evidence_point = {
        **entity(11, "point", "L_EVIDENCE", {"coords_m": [[2.5, 1.5]]}),
        "evidence_id": EVIDENCE_ID,
    }
    extra = [
        evidence_point,
        symbol(12, "evidence_marker", "L_EVIDENCE", (3.5, 2.5), attributes={"evidence_number": 2}),
        symbol(13, "generic_vehicle", "L_SYMBOLS", (8.0, 1.5), attributes={"label": "V1"}),
        symbol(14, "north_arrow", "L_ORIENTATION", (7.0, 4.5)),
        symbol(15, "control_point", "L_CONTROL", (CONTROL_SEGMENT_M, -3.0)),
        entity(16, "line", "L_CONTROL", {"coords_m": [[0.0, -3.0], [CONTROL_SEGMENT_M, -3.0]]}),
        dimension(17, (0.0, 3.125), (4.2, 3.125), 4.2),
        dimension(18, (-1.5, -2.0), (-1.5, 5.0), None),
        dimension(19, (6.0, -2.0), (6.0, 5.0), 6.5),
        entity(
            20,
            "text",
            "L_ANNOTATIONS",
            {
                "anchor_m": [-1.0, 5.5],
                "rotation_deg": 0.0,
                "height_mm_paper": 3.5,
                "content": "Croquis de prueba (golden)",
            },
        ),
    ]
    snap = snapshot([*mixed_entities(), *extra])
    snap["evidence_table"] = [
        {
            "evidence_id": EVIDENCE_ID,
            "evidence_number": 1,
            "entity_id": evidence_point["id"],
            "label": "Casquillo",
            "x_m": 2.5,
            "y_m": 1.5,
            "metric_origin": "scaled_drawing",
        }
    ]
    return snap


def golden_bytes() -> bytes:
    """Final-mode export of :func:`golden_snapshot`, frozen at the fixture time."""
    return export_dxf(
        golden_snapshot(),
        default_catalog(),
        draft=False,
        generator_version=GENERATOR_VERSION,
        frozen_at=FROZEN_AT,
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(data: bytes) -> Drawing:
    return read_drawing(io.StringIO(data.decode("utf-8")))


def has_entity_id(graphic: Any) -> bool:
    return graphic.has_xdata(APP_ID) and any(
        code == 1000 and value.startswith("entity_id=") for code, value in graphic.get_xdata(APP_ID)
    )


def test_committed_golden_dxf_equals_a_fresh_export() -> None:
    assert GOLDEN_PATH.is_file(), REGENERATE_HINT
    committed = GOLDEN_PATH.read_bytes()
    assert sha256(committed) == GOLDEN_SHA256, REGENERATE_HINT
    assert committed == golden_bytes(), REGENERATE_HINT


def test_golden_dxf_covers_every_entity_kind_and_layer() -> None:
    doc = read(GOLDEN_PATH.read_bytes())
    assert len(doc.audit().errors) == 0
    msp = doc.modelspace()
    written = [graphic for graphic in msp if has_entity_id(graphic)]
    assert Counter(graphic.dxf.layer for graphic in written) == EXPECTED_LAYER_COUNTS
    kinds = {graphic.dxftype() for graphic in written}
    assert kinds == {"LINE", "LWPOLYLINE", "POINT", "TEXT", "INSERT", "DIMENSION"}
    assert not msp.query("TEXT[layer=='L_SHEET']"), "final mode has no draft watermark"
    markers: list[Any] = list(msp.query("INSERT[name=='S2C_evidence_marker']"))
    marker_numbers = {insert.get_attrib_text("EVIDENCE_NUMBER") for insert in markers}
    assert marker_numbers == {"1", "2"}
    texts = {dim.dxf.text for dim in msp.query("DIMENSION")}
    assert texts == {"4.20", "4.24", "6.50", "7.00"}
    styles = Counter(dim.dxf.dimstyle for dim in msp.query("DIMENSION"))
    assert styles == {"S2C_DIM": 3, "S2C_DIM_ALERT": 1}


def test_control_segment_is_five_metres_in_model_units() -> None:
    doc = read(GOLDEN_PATH.read_bytes())
    assert doc.header["$INSUNITS"] == 6  # metres
    (control,) = doc.modelspace().query("LINE[layer=='L_CONTROL']")
    length = control.dxf.start.distance(control.dxf.end)
    assert math.isclose(length, CONTROL_SEGMENT_M, abs_tol=1e-9)
