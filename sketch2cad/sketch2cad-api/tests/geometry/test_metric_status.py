"""H3 metric status rule of spec section 11.3 (H3-8, REQ-07, US-11)."""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from sketch2cad.geometry.metric_status import (
    MetricEntity,
    MetricStatusResult,
    apply_metric_status,
    blocking_reason,
    compute_metric_status,
)

SCHEMATIC = "schematic_sketch"
SCALED = "scaled_drawing"
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
ASSIGNMENT = re.compile(r"metric_status\s*=")


@dataclass
class Holder:
    metric_status: str


def wall(entity_id: str, origin: str, layer: str = "L_STRUCTURE", **extra: bool) -> MetricEntity:
    return MetricEntity(entity_id, "polyline", layer, origin, **extra)


def test_trace_only_in_structure_is_approximate_until_declared_and_non_metric_uncalibrated() -> (
    None
):
    traced = [wall("w1", "trace_only")]
    declared = [wall("w1", "declared_dimension")]

    assert compute_metric_status(SCHEMATIC, "scan_dpi", False, traced) == MetricStatusResult(
        "approximate", ("w1",)
    )
    assert compute_metric_status(SCHEMATIC, "scan_dpi", False, declared) == MetricStatusResult(
        "metric", ()
    )
    assert compute_metric_status(SCHEMATIC, None, False, traced) == MetricStatusResult(
        "non_metric", ()
    )


def test_blocking_entities_list_exactly_the_offending_ids_with_their_reasons() -> None:
    entities = [
        wall("wall-ok", "declared_dimension"),
        wall("wall-traced", "trace_only"),
        wall("door-none", "none", layer="L_ACCESS"),
        wall("door-ok", "triangulation", layer="L_ACCESS"),
        wall("tree", "none", layer="L_SYMBOLS"),
        wall("note", "trace_only", layer="L_ANNOTATIONS"),
        wall("marker", "trace_only", layer="L_EVIDENCE", references_evidence=True),
        wall("marker-ok", "baseline_offset", layer="L_EVIDENCE", references_evidence=True),
        wall("gone", "trace_only", deleted=True),
    ]

    result = compute_metric_status(SCHEMATIC, "scan_segment", False, entities)

    assert result.status == "approximate"
    assert result.blocking_entities == ("wall-traced", "door-none", "marker")
    assert dict(result.reasons) == {
        "wall-traced": "trace_only",
        "door-none": "none",
        "marker": "trace_only",
    }


@pytest.mark.parametrize("layer", ["L_EVIDENCE", "L_SYMBOLS", "L_TEXT", "L_DIMENSIONS"])
@pytest.mark.parametrize("origin", ["trace_only", "none"])
def test_an_entity_referencing_evidence_blocks_in_any_layer(layer: str, origin: str) -> None:
    linked = wall("linked", origin, layer=layer, references_evidence=True)
    unlinked = wall("unlinked", origin, layer=layer)

    assert compute_metric_status(SCHEMATIC, "scan_dpi", False, [linked]).blocking_entities == (
        "linked",
    )
    assert blocking_reason(linked) == origin
    assert blocking_reason(unlinked) is None


@pytest.mark.parametrize(
    "origin",
    ["declared_dimension", "triangulation", "baseline_offset", "scaled_drawing", "derived"],
)
def test_metric_origins_do_not_block_evidence_entities(origin: str) -> None:
    marker = wall("marker", origin, layer="L_EVIDENCE", references_evidence=True)

    assert blocking_reason(marker) is None
    assert compute_metric_status(SCHEMATIC, "scan_dpi", False, [marker]).status == "metric"


def test_a_discrepant_dimension_is_reported_but_does_not_block_metric() -> None:
    dimension = MetricEntity("dim", "dimension", "L_DIMENSIONS", "none", discrepancy=True)
    entities = [wall("w1", "declared_dimension"), dimension]

    result = compute_metric_status(SCHEMATIC, "scan_dpi", False, entities)

    assert result == MetricStatusResult("metric", ())
    assert result.discrepant_dimensions == ("dim",)


def test_discrepancies_are_reported_whatever_the_status() -> None:
    dimension = MetricEntity("dim", "dimension", "L_DIMENSIONS", "none", discrepancy=True)
    deleted = MetricEntity(
        "old", "dimension", "L_DIMENSIONS", "none", deleted=True, discrepancy=True
    )

    uncalibrated = compute_metric_status(SCHEMATIC, None, False, [dimension, deleted])
    approximate = compute_metric_status(SCALED, "scan_dpi", True, [wall("w", "none"), dimension])

    assert (uncalibrated.status, uncalibrated.discrepant_dimensions) == ("non_metric", ("dim",))
    assert (approximate.status, approximate.discrepant_dimensions) == ("approximate", ("dim",))


def test_apply_metric_status_stores_the_converted_status() -> None:
    plan = Holder("non_metric")

    stored = apply_metric_status(plan, MetricStatusResult("metric"), str.upper)

    assert (stored, plan.metric_status) == ("METRIC", "METRIC")


def test_metric_status_is_assigned_only_in_its_module() -> None:
    """Criterion: ``rg -n 'metric_status\\s*=' src/ | rg -v 'metric_status.py|models/'``."""
    hits = [
        f"{path.relative_to(SRC_DIR)}:{number}: {line.strip()}"
        for path in sorted(SRC_DIR.rglob("*.py"))
        if path.name != "metric_status.py" and "models" not in path.relative_to(SRC_DIR).parts
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if ASSIGNMENT.search(line)
    ]

    assert hits == []
