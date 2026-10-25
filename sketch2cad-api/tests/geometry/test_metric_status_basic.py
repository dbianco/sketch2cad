"""Initial metric status rules and provenance defaults (spec 11.3, REQ-07, US-11)."""

import pytest

from sketch2cad.geometry.metric_status import (
    MetricEntity,
    MetricStatusResult,
    compute_metric_status,
    default_metric_origin,
)

SCALED = "scaled_drawing"
SCHEMATIC = "schematic_sketch"
METRIC_METHODS = ["scan_dpi", "scan_segment", "photo_homography_4pt"]


def line(
    entity_id: str = "e1",
    origin: str = "none",
    layer: str = "L_STRUCTURE",
    **extra: bool,
) -> MetricEntity:
    return MetricEntity(entity_id, "line", layer, origin, **extra)


def drawn_line(source_mode: str, method: str | None, as_metric: bool) -> MetricEntity:
    """A line drawn by hand, with the provenance default of the plan."""
    return line(origin=default_metric_origin(source_mode, method, as_metric, "line"))


@pytest.mark.parametrize("source_mode", [SCALED, SCHEMATIC])
@pytest.mark.parametrize("entities", [[], [line(origin="scaled_drawing")], [line()]])
def test_calibration_method_none_is_non_metric(
    source_mode: str, entities: list[MetricEntity]
) -> None:
    result = compute_metric_status(source_mode, "none", False, entities)

    assert result == MetricStatusResult("non_metric", ())


@pytest.mark.parametrize("source_mode", [SCALED, SCHEMATIC])
def test_a_plan_without_calibration_is_non_metric(source_mode: str) -> None:
    result = compute_metric_status(source_mode, None, False, [line(origin="scaled_drawing")])

    assert result == MetricStatusResult("non_metric", ())


@pytest.mark.parametrize("method", METRIC_METHODS)
def test_scaled_drawing_with_metric_calibration_draws_scaled_drawing_lines_and_is_metric(
    method: str,
) -> None:
    entity = drawn_line(SCALED, method, True)

    result = compute_metric_status(SCALED, method, True, [entity])

    assert entity.metric_origin == "scaled_drawing"
    assert result == MetricStatusResult("metric", ())


@pytest.mark.parametrize("as_metric", [True, False])
def test_schematic_sketch_draws_none_lines_that_block_metric(as_metric: bool) -> None:
    entity = drawn_line(SCHEMATIC, "scan_dpi", as_metric)

    result = compute_metric_status(SCHEMATIC, "scan_dpi", as_metric, [entity])

    assert entity.metric_origin == "none"
    assert result == MetricStatusResult("approximate", ("e1",))


def test_scaled_drawing_not_accepted_as_metric_draws_none_lines_and_is_approximate() -> None:
    entity = drawn_line(SCALED, "scan_dpi", False)

    result = compute_metric_status(SCALED, "scan_dpi", False, [entity])

    assert entity.metric_origin == "none"
    assert result == MetricStatusResult("approximate", ("e1",))


@pytest.mark.parametrize(
    ("source_mode", "method", "as_metric"),
    [(SCALED, None, False), (SCALED, "none", False), (SCHEMATIC, None, False)],
)
def test_without_metric_calibration_hand_drawn_entities_are_none(
    source_mode: str, method: str | None, as_metric: bool
) -> None:
    for entity_type in ["line", "polyline", "point", "text", "dimension", "symbol"]:
        assert default_metric_origin(source_mode, method, as_metric, entity_type) == "none"


def test_as_metric_without_a_calibration_method_never_gives_scaled_drawing() -> None:
    assert default_metric_origin(SCALED, None, True, "line") == "none"
    assert default_metric_origin(SCALED, "none", True, "line") == "none"


@pytest.mark.parametrize(
    ("source_mode", "as_metric", "status"),
    [(SCALED, True, "metric"), (SCALED, False, "approximate"), (SCHEMATIC, True, "approximate")],
)
def test_a_calibrated_plan_without_entities(source_mode: str, as_metric: bool, status: str) -> None:
    result = compute_metric_status(source_mode, "scan_dpi", as_metric, [])

    assert result == MetricStatusResult(status, ())


def test_deleted_entities_are_ignored() -> None:
    entities = [line("kept", "scaled_drawing"), line("gone", "none", deleted=True)]

    result = compute_metric_status(SCALED, "scan_dpi", True, entities)

    assert result == MetricStatusResult("metric", ())


def test_a_deleted_blocking_entity_in_a_schematic_sketch_leaves_no_blocker() -> None:
    result = compute_metric_status(SCHEMATIC, "scan_dpi", False, [line(deleted=True)])

    assert result == MetricStatusResult("approximate", ())


@pytest.mark.parametrize("layer", ["L_DIMENSIONS", "L_TEXT", "L_SYMBOLS", "L_ANNOTATIONS"])
def test_non_metric_entities_outside_structure_and_access_do_not_block(layer: str) -> None:
    entities = [line("wall", "scaled_drawing"), line("other", "none", layer=layer)]

    result = compute_metric_status(SCALED, "scan_dpi", True, entities)

    assert result == MetricStatusResult("metric", ())


def test_a_dimension_entity_in_a_schematic_sketch_is_not_blocking() -> None:
    dimension = MetricEntity("dim", "dimension", "L_DIMENSIONS", "none")

    result = compute_metric_status(SCHEMATIC, "scan_dpi", False, [dimension, line("wall")])

    assert result == MetricStatusResult("approximate", ("wall",))


def test_entities_referencing_evidence_block_in_any_layer() -> None:
    marker = MetricEntity("ev", "point", "L_EVIDENCE", "none", references_evidence=True)

    result = compute_metric_status(
        SCALED, "scan_dpi", True, [line("wall", "scaled_drawing"), marker]
    )

    assert result == MetricStatusResult("approximate", ("ev",))


@pytest.mark.parametrize("origin", ["trace_only", "none"])
def test_trace_only_and_none_block_in_access(origin: str) -> None:
    door = line("door", origin, layer="L_ACCESS")

    result = compute_metric_status(SCALED, "scan_dpi", True, [door])

    assert result == MetricStatusResult("approximate", ("door",))


@pytest.mark.parametrize(
    "origin",
    ["declared_dimension", "triangulation", "baseline_offset", "scaled_drawing", "derived"],
)
def test_every_metric_origin_counts_as_metric_once_calibrated(origin: str) -> None:
    result = compute_metric_status(SCHEMATIC, "scan_dpi", False, [line(origin=origin)])

    assert result == MetricStatusResult("metric", ())


def test_blocking_entities_keep_the_input_order() -> None:
    entities = [line("b"), line("ok", "scaled_drawing"), line("a", "trace_only"), line("c")]

    result = compute_metric_status(SCALED, "scan_dpi", True, entities)

    assert result.blocking_entities == ("b", "a", "c")
