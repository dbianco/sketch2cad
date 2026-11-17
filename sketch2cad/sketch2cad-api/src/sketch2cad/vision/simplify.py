"""Polyline simplification, length filter and confidence of the traced skeleton paths.

Confidence (spec section 10, ``confidence = f(length, straightness)``)::

    confidence = clamp(0.5 * length_score + 0.5 * straightness, 0, 1)
    length_score = 1 - exp(-length_mm / 20)
    straightness = chord / path length of the ORIGINAL pixel path (before simplification)

A 5 mm straight stroke scores 0.61, a 50 mm straight stroke 0.96; a closed loop has
straightness 0, so its confidence is at most 0.5. The score ranks suggestions for review;
it is not a probability and never makes a suggestion metric.
"""

import math
from collections.abc import Sequence

from shapely import LineString

LENGTH_SCALE_MM = 20.0
"""Paper mm at which the length score reaches 1 - 1/e (0.63)."""

PointXY = tuple[float, float]


def simplify_path(path: Sequence[tuple[int, int]], tolerance_px: float) -> list[PointXY]:
    """Douglas-Peucker simplification of a pixel path with ``tolerance_px`` (shapely).

    Endpoints are kept, so a closed path stays closed. Paths of fewer than two points
    return them unchanged.
    """
    points = [(float(x), float(y)) for x, y in path]
    if len(points) < 2:
        return points
    simplified = LineString(points).simplify(tolerance_px, preserve_topology=False)
    return [(float(x), float(y)) for x, y in simplified.coords]


def polyline_length(points: Sequence[PointXY]) -> float:
    """Sum of the segment lengths of ``points``."""
    return sum(math.dist(a, b) for a, b in zip(points, points[1:], strict=False))


def straightness(path: Sequence[tuple[int, int]]) -> float:
    """Chord over path length of the pixel path, in [0, 1] (0 for a closed or empty path)."""
    floats = [(float(x), float(y)) for x, y in path]
    length = polyline_length(floats)
    if length <= 0:
        return 0.0
    return min(1.0, math.dist(floats[0], floats[-1]) / length)


def confidence(length_mm: float, path_straightness: float) -> float:
    """Confidence in [0, 1] of a suggestion (see the module docstring)."""
    length_score = 1.0 - math.exp(-max(0.0, length_mm) / LENGTH_SCALE_MM)
    value = 0.5 * length_score + 0.5 * path_straightness
    return min(1.0, max(0.0, value))
