"""Hand-solved triangulation and baseline-offset cases (CA-03, H3-1, REQ-06, US-08, US-09).

World metres, Y up; "left" of A→B is its counter-clockwise normal. Every expected value
below is worked out by hand in the comment of its case and must match within 1 cm.
"""

import math

import pytest

from sketch2cad.geometry.homography import Point
from sketch2cad.geometry.metrics import (
    Side,
    Solution,
    baseline_offset_point,
    fix_polyline_length,
    triangulate,
)

HAND_TOLERANCE_M = 0.01


def assert_close(actual: Point | None, expected: Point) -> None:
    assert actual is not None
    assert math.dist(actual, expected) <= HAND_TOLERANCE_M, (actual, expected)


# Triangulation: d = |AB|, x = (d² + dA² − dB²) / (2d) along û, h = √(dA² − x²) along n̂.
TRIANGULATION_CASES: list[tuple[str, Point, Point, float, float, Solution, Point, Point]] = [
    # 3-4-5: d = 5, x = (25 + 9 − 16) / 10 = 1.8, h = √(9 − 3.24) = 2.4.
    # û = (1, 0), left n̂ = (0, 1): P = (1.8, 2.4); hint above the baseline picks it.
    ("3-4-5 nearest", (0, 0), (5, 0), 3.0, 4.0, "nearest", (2, 2), (1.8, 2.4)),
    # Same triangle, alt is the mirror image below the baseline: (1.8, −2.4).
    ("3-4-5 alt", (0, 0), (5, 0), 3.0, 4.0, "alt", (2, 2), (1.8, -2.4)),
    # Symmetric: d = 6, dA = dB = 5, x = (36 + 25 − 25) / 12 = 3, h = √(25 − 9) = 4.
    # The hint sits below the baseline, so nearest is (3, −4).
    ("symmetric 5-5-6", (0, 0), (6, 0), 5.0, 5.0, "nearest", (3, -1), (3, -4)),
    # Vertical baseline from (1, 1) to (1, 6): û = (0, 1), left n̂ = (−1, 0).
    # x = 1.8, h = 2.4: foot (1, 2.8); left (−1.4, 2.8), right (3.4, 2.8); hint on the right.
    ("vertical 3-4-5", (1, 1), (1, 6), 3.0, 4.0, "nearest", (3, 3), (3.4, 2.8)),
    # Field case: corners 4 m apart, both tapes 2.50 m: x = 2, h = √(6.25 − 4) = 1.5.
    ("isosceles 2.5-2.5-4", (0, 0), (4, 0), 2.5, 2.5, "nearest", (2, 1), (2, 1.5)),
    # Tangent: 2 + 3 = 5 = d, one solution at A + 2·û = (2, 0) for both choices.
    ("tangent 2+3=5", (0, 0), (5, 0), 2.0, 3.0, "alt", (0, 9), (2, 0)),
]


@pytest.mark.parametrize(
    ("a", "b", "d_a", "d_b", "solution", "hint", "expected"),
    [case[1:] for case in TRIANGULATION_CASES],
    ids=[case[0] for case in TRIANGULATION_CASES],
)
def test_triangulation_matches_hand_solution(
    a: Point, b: Point, d_a: float, d_b: float, solution: Solution, hint: Point, expected: Point
) -> None:
    result = triangulate(a, b, d_a, d_b, solution, hint)
    assert_close(result.point, expected)


# Baseline plus offset: P = A + s·û + d·n̂, left n̂ = (−ûy, ûx), right n̂ = (ûy, −ûx).
BASELINE_CASES: list[tuple[str, Point, Point, float, float, Side, Point]] = [
    # û = (1, 0), left n̂ = (0, 1): P = (0 + 1.5, 0 + 0.8) = (1.5, 0.8).
    ("road left", (0, 0), (10, 0), 1.5, 0.8, "left", (1.5, 0.8)),
    # Right n̂ = (0, −1): P = (1.5, −0.8).
    ("road right", (0, 0), (10, 0), 1.5, 0.8, "right", (1.5, -0.8)),
    # A = (2, 1), B = (5, 5): |AB| = 5, û = (0.6, 0.8), left n̂ = (−0.8, 0.6).
    # P = (2 + 5·0.6 − 2·0.8, 1 + 5·0.8 + 2·0.6) = (3.4, 6.2).
    ("oblique left", (2, 1), (5, 5), 5.0, 2.0, "left", (3.4, 6.2)),
    # Right n̂ = (0.8, −0.6): P = (2 + 3 + 1.6, 1 + 4 − 1.2) = (6.6, 3.8).
    ("oblique right", (2, 1), (5, 5), 5.0, 2.0, "right", (6.6, 3.8)),
    # Heading south: û = (0, −1), left n̂ = (1, 0) (east). s = −2 goes back north.
    # P = (0 + 0 + 3, 0 + 2 + 0) = (3, 2).
    ("south negative s", (0, 0), (0, -4), -2.0, 3.0, "left", (3, 2)),
    # d = 0 lies on the baseline: P = A + 2.5·û = (2.5, 0) whatever the side.
    ("on baseline", (0, 0), (4, 0), 2.5, 0.0, "right", (2.5, 0)),
]


@pytest.mark.parametrize(
    ("a", "b", "s", "d", "side", "expected"),
    [case[1:] for case in BASELINE_CASES],
    ids=[case[0] for case in BASELINE_CASES],
)
def test_baseline_offset_matches_hand_solution(
    a: Point, b: Point, s: float, d: float, side: Side, expected: Point
) -> None:
    assert_close(baseline_offset_point(a, b, s, d, side), expected)


def test_fix_length_of_3_m_segment_to_4_20_m() -> None:
    # H3-6: anchor (0, 0), free vertex (3, 0) moves to (4.2, 0), a shift of (1.2, 0);
    # the next vertex (3, 2) moves by the same vector to (4.2, 2).
    result = fix_polyline_length([(0, 0), (3, 0), (3, 2)], 0, 1, 4.20)
    for actual, expected in zip(result, [(0, 0), (4.2, 0), (4.2, 2)], strict=True):
        assert_close(actual, expected)
