"""Metric geometry functions and the constraint dependency graph (H3-1, H3-2, H3-4, V-04)."""

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from sketch2cad.geometry.constraints import (
    dependents_breadth_first,
    find_dependency_cycle,
    has_dependency_cycle,
    would_create_cycle,
)
from sketch2cad.geometry.homography import Point
from sketch2cad.geometry.metrics import (
    DISTANCE_MESSAGE,
    TANGENCY_TOLERANCE_M,
    DegenerateGeometryError,
    baseline_offset_point,
    circle_intersections,
    fix_polyline_length,
    parse_distance,
    scale_from_anchor,
    triangulate,
)

PROPERTY_TOLERANCE_M = 1e-9
coordinate = st.floats(min_value=-500, max_value=500, allow_nan=False, allow_infinity=False)
points = st.tuples(coordinate, coordinate)


def signed_offset(a: Point, b: Point, p: Point) -> tuple[float, float]:
    """Projection of AP on û and its signed perpendicular distance (positive = left)."""
    length = math.dist(a, b)
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    vx, vy = p[0] - a[0], p[1] - a[1]
    return (vx * ux + vy * uy, ux * vy - uy * vx)


@given(a=points, b=points, p=points)
def test_triangulated_points_are_at_the_declared_distances(a: Point, b: Point, p: Point) -> None:
    assume(math.dist(a, b) > 1e-3)
    d_a, d_b = math.dist(a, p), math.dist(b, p)
    assume(d_a > 1e-3 and d_b > 1e-3)
    result = triangulate(a, b, d_a, d_b, "nearest", hint=p)
    assert result.candidates
    for candidate in result.candidates:
        assert abs(math.dist(candidate, a) - d_a) <= PROPERTY_TOLERANCE_M
        assert abs(math.dist(candidate, b) - d_b) <= PROPERTY_TOLERANCE_M
    if math.dist(a, b) >= 1 and abs(signed_offset(a, b, p)[1]) >= 0.1:
        # Well-conditioned configuration: the nearest candidate is the original point.
        assert result.point is not None
        assert math.dist(result.point, p) <= 1e-6


@given(
    a=points,
    b=points,
    s=st.floats(min_value=-100, max_value=100),
    d=st.floats(min_value=0, max_value=100),
    side=st.sampled_from(["left", "right"]),
)
def test_baseline_offset_has_the_declared_projection_and_offset(
    a: Point, b: Point, s: float, d: float, side: str
) -> None:
    assume(math.dist(a, b) > 1e-3)
    p = baseline_offset_point(a, b, s, d, "left" if side == "left" else "right")
    along, across = signed_offset(a, b, p)
    assert abs(along - s) <= PROPERTY_TOLERANCE_M
    assert abs(across - (d if side == "left" else -d)) <= PROPERTY_TOLERANCE_M


@given(
    anchor=points,
    point=points,
    length=st.floats(min_value=1e-3, max_value=1000),
)
def test_scale_from_anchor_keeps_direction_and_sets_length(
    anchor: Point, point: Point, length: float
) -> None:
    assume(math.dist(anchor, point) > 1e-3)
    moved = scale_from_anchor(anchor, point, length)
    assert abs(math.dist(anchor, moved) - length) <= PROPERTY_TOLERANCE_M
    assert abs(signed_offset(anchor, point, moved)[1]) <= PROPERTY_TOLERANCE_M


@pytest.mark.parametrize(
    ("ra", "rb", "expected"),
    [(2.0, 3.0, (2.0, 0.0)), (7.0, 2.0, (7.0, 0.0)), (2.0, 7.0, (-2.0, 0.0)), (0.0, 5.0, (0, 0))],
    ids=["external", "internal", "internal-small-a", "zero-radius-on-circle"],
)
def test_tangent_circles_give_one_solution(
    ra: float, rb: float, expected: tuple[float, float]
) -> None:
    result = circle_intersections((0, 0), ra, (5, 0), rb)
    assert result.kind == "tangent"
    assert len(result.points) == 1
    assert math.dist(result.points[0], expected) <= 1e-12


def test_tangency_within_tolerance_still_gives_one_solution() -> None:
    result = circle_intersections((0, 0), 2.0, (5 + TANGENCY_TOLERANCE_M / 2, 0), 3.0)
    assert result.kind == "tangent"
    assert len(result.points) == 1


@pytest.mark.parametrize(
    ("b", "ra", "rb", "kind"),
    [
        ((10, 0), 2.0, 3.0, "disjoint"),
        ((1, 0), 5.0, 1.0, "contained"),
        ((0, 0), 2.0, 3.0, "concentric"),
        ((0, 0), 2.0, 2.0, "concentric"),
    ],
    ids=["disjoint", "contained", "concentric", "identical"],
)
def test_degenerate_circles_give_no_solution(
    b: tuple[float, float], ra: float, rb: float, kind: str
) -> None:
    result = circle_intersections((0, 0), ra, b, rb)
    assert result.kind == kind
    assert result.points == ()
    assert not result.intersects
    triangulation = triangulate((0, 0), b, ra, rb)
    assert triangulation.point is None and triangulation.nearest_index is None


def test_two_solutions_are_left_then_right_and_alt_without_hint() -> None:
    result = circle_intersections((0, 0), 3.0, (5, 0), 4.0)
    assert result.kind == "two"
    assert result.points[0][1] > 0 > result.points[1][1]
    assert triangulate((0, 0), (5, 0), 3.0, 4.0, "alt").point == result.points[1]


@pytest.mark.parametrize("radius", [-1.0, math.inf, math.nan])
def test_invalid_radius_raises(radius: float) -> None:
    with pytest.raises(ValueError, match="ra must be"):
        circle_intersections((0, 0), radius, (5, 0), 1.0)


def test_triangulation_rejects_non_positive_distance() -> None:
    with pytest.raises(ValueError, match="d_a must be"):
        triangulate((0, 0), (5, 0), 0.0, 1.0)


def test_coincident_baseline_and_zero_length_segment_raise() -> None:
    with pytest.raises(DegenerateGeometryError):
        baseline_offset_point((1, 1), (1, 1), 1.0, 1.0, "left")
    with pytest.raises(DegenerateGeometryError):
        scale_from_anchor((1, 1), (1, 1), 2.0)
    with pytest.raises(ValueError, match="target_length"):
        scale_from_anchor((0, 0), (1, 0), 0.0)


def test_fix_length_with_anchor_after_free_vertex_moves_earlier_vertices() -> None:
    result = fix_polyline_length([(0, 2), (0, 0), (3, 0), (6, 0)], 2, 1, 4.2)
    assert result[2:] == [(3, 0), (6, 0)]
    assert math.dist(result[1], (-1.2, 0)) <= 1e-12
    assert math.dist(result[0], (-1.2, 2)) <= 1e-12
    with pytest.raises(ValueError, match="consecutive"):
        fix_polyline_length([(0, 0), (1, 0), (2, 0)], 0, 2, 1.0)


@pytest.mark.parametrize(
    ("text", "value"),
    [("3,50", 3.5), ("3.50", 3.5), (" 4,2 ", 4.2), ("12", 12.0), (",5", 0.5), ("+1.", 1.0)],
)
def test_parse_distance_accepts_comma_or_point(text: str, value: float) -> None:
    assert parse_distance(text) == value


@pytest.mark.parametrize(
    "text",
    ["0", "0,00", "-3,5", "", "  ", "abc", "3,5 m", "1.234,5", "1e3", "inf", "nan", "9" * 400],
)
def test_parse_distance_rejects_with_the_v04_message(text: str) -> None:
    with pytest.raises(ValueError) as error:
        parse_distance(text)
    assert str(error.value) == DISTANCE_MESSAGE
    assert DISTANCE_MESSAGE == "Ingresa una distancia numérica mayor que cero, por ejemplo 3,50."


def test_self_loop_is_a_cycle() -> None:
    assert has_dependency_cycle({"p": ["p"]})
    assert find_dependency_cycle({"p": ["p"]}) == ["p", "p"]


def test_two_cycle_is_detected_with_its_path() -> None:
    graph = {"a": ["x"], "x": ["b"], "b": ["x"]}
    assert has_dependency_cycle(graph)
    assert find_dependency_cycle(graph) == ["x", "b", "x"]


def test_diamond_without_cycle() -> None:
    graph = {"p": ["a", "b"], "a": ["c"], "b": ["c"], "c": []}
    assert not has_dependency_cycle(graph)
    assert find_dependency_cycle(graph) is None
    assert not has_dependency_cycle({})


def test_long_chain_does_not_hit_the_recursion_limit() -> None:
    chain = {i: [i + 1] for i in range(5000)}
    assert not has_dependency_cycle(chain)
    chain[5000] = [0]
    assert has_dependency_cycle(chain)


def test_would_create_cycle_checks_direct_and_indirect_dependence() -> None:
    graph = {"q": ["p", "a"], "r": ["q", "b"]}
    assert would_create_cycle(graph, "p", ["r", "c"])
    assert would_create_cycle(graph, "p", ["p", "a"])
    assert not would_create_cycle(graph, "p", ["a", "b"])
    assert not would_create_cycle(graph, "q", ["a", "b"])


def test_dependents_breadth_first_is_ordered_and_cycle_safe() -> None:
    graph = {"q": ["p", "a"], "r": ["q", "b"], "s": ["p", "r"]}
    assert dependents_breadth_first(graph, "p") == ["q", "s", "r"]
    assert dependents_breadth_first({"x": ["y"], "y": ["x"]}, "x") == ["y"]
    assert dependents_breadth_first(graph, "zzz") == []
