"""Connected vertices that follow a ``fix_length`` (:mod:`sketch2cad.entities.connected`)."""

import math
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sketch2cad.entities.connected import (
    Neighbour,
    connected_tolerance_m,
    propagate,
    vertices,
)
from sketch2cad.entities.ops_fix_length import fix_length
from sketch2cad.plans.local_frame import PROVISIONAL_PAPER_SCALE

TOL = 0.1


def line(a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any]:
    return {"kind": "line", "coords_m": [list(a), list(b)]}


def rectangle(width: float = 4.0, height: float = 3.0) -> dict[str, dict[str, Any]]:
    """Four separate lines: top, right, bottom, left; corners shared exactly."""
    tl, tr, br, bl = (0.0, height), (width, height), (width, 0.0), (0.0, 0.0)
    return {
        "top": line(tl, tr),
        "right": line(tr, br),
        "bottom": line(br, bl),
        "left": line(bl, tl),
    }


def fix_top(walls: dict[str, dict[str, Any]], value: float) -> dict[str, Any]:
    return fix_length(walls["top"], {}, 0, 1, value).geometry


def run(
    walls: dict[str, dict[str, Any]], new_top: dict[str, Any], locked: frozenset[str] = frozenset()
):
    neighbours = [Neighbour(k, g, k in locked) for k, g in walls.items() if k != "top"]
    return propagate("top", walls["top"], new_top, neighbours, TOL)


def test_tolerance_is_one_paper_millimetre() -> None:
    assert connected_tolerance_m(None) == pytest.approx(0.1)
    assert connected_tolerance_m(50) == pytest.approx(0.05)
    assert connected_tolerance_m(None) == connected_tolerance_m(PROVISIONAL_PAPER_SCALE)


def test_fixing_the_top_of_4_lines_moves_the_right_wall_top_end_only() -> None:
    walls = rectangle()
    new_top = fix_top(walls, 4.8)

    result = run(walls, new_top)

    assert set(result.geometries) == {"right"}
    assert result.geometries["right"]["coords_m"] == [[4.8, 3.0], [4.0, 0.0]]
    assert [(m.entity_id, m.vertex, m.new) for m in result.moves] == [("right", 0, (4.8, 3.0))]
    # The moved corner coincides exactly with the free end of the fixed side.
    assert tuple(new_top["coords_m"][1]) == result.moves[0].new
    assert result.pinned == ()


def test_a_corner_within_tolerance_moves_by_the_same_vector() -> None:
    walls = rectangle()
    walls["right"] = line((4.03, 3.02), (4.0, 0.0))

    result = run(walls, fix_top(walls, 4.8))

    assert result.geometries["right"]["coords_m"][0] == pytest.approx([4.83, 3.02])


def test_a_vertex_beyond_tolerance_stays() -> None:
    walls = rectangle()
    walls["right"] = line((4.2, 3.0), (4.0, 0.0))

    assert run(walls, fix_top(walls, 4.8)).geometries == {}


def test_a_locked_wall_is_pinned_and_reported_with_its_gap() -> None:
    walls = rectangle()

    result = run(walls, fix_top(walls, 4.8), locked=frozenset({"right"}))

    assert result.geometries == {}
    assert [(p.entity_id, p.vertex) for p in result.pinned] == [("right", 0)]
    assert result.pinned[0].gap_m == pytest.approx(0.8)


def test_a_chain_of_corners_follows_a_polyline_moved_rigidly() -> None:
    # An open polyline (left, top, right walls) and a separate bottom line closing it.
    poly = {"kind": "polyline", "coords_m": [[0, 0], [0, 3], [4, 3], [4, 0]], "closed": False}
    bottom = line((4.0, 0.0), (0.0, 0.0))
    dimension = {"kind": "dimension", "p1_m": [4.0, 0.0], "p2_m": [0.0, 0.0], "offset_m": -0.5}
    new = fix_length(poly, {}, 1, 2, 4.8).geometry  # top side; vertices 2 and 3 move +0.8 x

    result = propagate(
        "poly",
        poly,
        new,
        [Neighbour("bottom", bottom), Neighbour("dim", dimension)],
        TOL,
    )

    assert result.geometries["bottom"]["coords_m"] == [[4.8, 0.0], [0.0, 0.0]]
    assert result.geometries["dim"]["p1_m"] == [4.8, 0.0]
    assert result.geometries["dim"]["measured_value_m"] == pytest.approx(4.8)


def test_a_t_junction_on_a_rigidly_moved_wall_is_carried_with_it() -> None:
    poly = {"kind": "polyline", "coords_m": [[0, 0], [0, 3], [4, 3], [4, 0]], "closed": False}
    interior = line((2.0, 1.5), (4.0, 1.5))  # ends on the right wall (segment 2)
    new = fix_length(poly, {}, 1, 2, 4.58).geometry

    result = propagate("poly", poly, new, [Neighbour("interior", interior)], TOL)

    assert result.geometries["interior"]["coords_m"] == [[2.0, 1.5], [pytest.approx(4.58), 1.5]]


def test_a_t_junction_slides_proportionally_on_a_stretched_wall() -> None:
    walls = rectangle()
    walls["interior"] = line((2.0, 1.5), (2.0, 3.0))  # ends at the middle of the top side
    new_top = fix_top(walls, 4.8)

    result = run(walls, new_top)

    assert result.geometries["interior"]["coords_m"][1] == pytest.approx([2.4, 3.0])


def test_a_second_round_moves_vertices_connected_to_a_moved_end() -> None:
    walls = rectangle()
    walls["interior"] = line((2.0, 1.5), (4.0, 1.5))  # ends on the right line, not on top
    walls["partition"] = line((4.0, 1.5), (4.0, 1.0))  # shares that end
    new_top = fix_top(walls, 4.8)

    result = run(walls, new_top)

    # The right line tilts (top end +0.8, bottom end fixed): at t = 0.5 the end moves +0.4.
    assert result.geometries["interior"]["coords_m"][1] == pytest.approx([4.4, 1.5])
    assert result.geometries["partition"]["coords_m"][0] == pytest.approx([4.4, 1.5])


def test_nothing_moves_when_the_length_does_not_change() -> None:
    walls = rectangle()
    result = run(walls, fix_top(walls, 4.0))
    assert (result.geometries, result.moves, result.pinned) == ({}, (), ())


def test_the_result_does_not_depend_on_the_neighbour_order() -> None:
    walls = rectangle()
    walls["interior"] = line((2.0, 1.5), (2.0, 3.0))
    new_top = fix_top(walls, 4.8)
    items = [Neighbour(k, g) for k, g in walls.items() if k != "top"]

    forward = propagate("top", walls["top"], new_top, items, TOL)
    backward = propagate("top", walls["top"], new_top, list(reversed(items)), TOL)

    assert forward.geometries == backward.geometries
    assert sorted(forward.moves, key=str) == sorted(backward.moves, key=str)


@given(
    width=st.floats(min_value=1.0, max_value=20.0),
    height=st.floats(min_value=1.0, max_value=20.0),
    value=st.floats(min_value=0.5, max_value=30.0),
)
def test_corners_of_a_rectangle_of_lines_stay_closed(
    width: float, height: float, value: float
) -> None:
    walls = rectangle(width, height)
    new_top = fix_top(walls, value)

    result = run(walls, new_top)
    final = {**walls, **result.geometries, "top": new_top}

    corners = [("top", 1, "right", 0), ("right", 1, "bottom", 0), ("bottom", 1, "left", 0)]
    corners.append(("left", 1, "top", 0))
    for a, i, b, j in corners:
        assert math.dist(vertices(final[a])[i], vertices(final[b])[j]) == 0.0
    assert math.dist(*vertices(final["top"])) == pytest.approx(value)
