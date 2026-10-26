"""Metric editor tools in world metres (spec section 11.3, REQ-06, US-07, US-08, US-09).

Pure functions with no database, FastAPI or I/O. Points are ``(x, y)`` world metres with
Y up, so "left" of a direction is its counter-clockwise normal.

- :func:`circle_intersections` and :func:`triangulate`: a point at distance ``d_a`` from
  A and ``d_b`` from B. Degenerate geometry (disjoint, contained, concentric circles) is a
  structured result with zero points, never an exception.
- :func:`baseline_offset_point`: ``P = A + s·û + d·n̂`` with ``û`` the unit direction A→B
  and ``n̂`` its left (counter-clockwise) or right normal.
- :func:`scale_from_anchor` and :func:`fix_polyline_length`: "fix length" keeps the
  direction from the anchor and stretches the segment to the declared value.
- :func:`parse_distance`: user-typed distances with decimal comma or point (V-04).

Invalid *input values* (non-finite coordinates, negative radii, non-positive lengths,
coincident baseline or segment points) raise ``ValueError``; :class:`DegenerateGeometryError`
is the subclass for inputs that are finite but define no direction.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from sketch2cad.geometry.homography import Point

TANGENCY_TOLERANCE_M: Final = 1e-9
"""Two circles whose centre distance is within this many metres of ``ra + rb`` or
``|ra − rb|`` are tangent and give exactly one solution."""

DISTANCE_MESSAGE: Final = "Ingresa una distancia numérica mayor que cero, por ejemplo 3,50."
"""User-facing message of V-04 (PRD), raised by :func:`parse_distance`."""

_DISTANCE_PATTERN: Final = re.compile(r"\+?(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)", re.ASCII)

IntersectionKind = Literal["two", "tangent", "disjoint", "contained", "concentric"]
Solution = Literal["nearest", "alt"]
Side = Literal["left", "right"]


class DegenerateGeometryError(ValueError):
    """Finite input that defines no direction (coincident baseline or segment points)."""


@dataclass(frozen=True, slots=True)
class CircleIntersection:
    """Intersection of two circles.

    ``kind`` is ``two`` (two points), ``tangent`` (one point), or ``disjoint`` (too far
    apart), ``contained`` (one circle inside the other) and ``concentric`` (same centre,
    including identical circles) with no points. With two points the first lies to the
    left of the direction A→B and the second to the right.
    """

    kind: IntersectionKind
    points: tuple[Point, ...]

    @property
    def intersects(self) -> bool:
        return bool(self.points)


@dataclass(frozen=True, slots=True)
class Triangulation:
    """Candidates of a triangulation and the one picked by ``solution``.

    ``point`` is ``None`` when the circles do not cross (V-06). ``nearest_index`` is the
    candidate closest to the hint (the current position of P), or 0 without hint.
    """

    intersection: CircleIntersection
    nearest_index: int | None
    point: Point | None

    @property
    def candidates(self) -> tuple[Point, ...]:
        return self.intersection.points


def _finite_point(point: Sequence[float], name: str) -> Point:
    if len(point) != 2 or not all(math.isfinite(v) for v in point):
        raise ValueError(f"{name} must be a finite (x, y) point, got {point!r}")
    return (float(point[0]), float(point[1]))


def _finite(value: float, name: str, *, positive: bool = False) -> float:
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        bound = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a {bound} finite number, got {value}")
    return float(value)


def _unit(a: Point, b: Point, what: str) -> tuple[float, float, float]:
    """Unit vector A→B and the distance; raises when the points coincide."""
    length = math.dist(a, b)
    if length == 0.0:
        raise DegenerateGeometryError(f"{what} points coincide")
    return ((b[0] - a[0]) / length, (b[1] - a[1]) / length, length)


def _tangent_position(d: float, ra: float, rb: float) -> float | None:
    """Signed position along A→B of the tangency point, or ``None`` if not tangent.

    The gap ``ε`` to exact tangency is split in half, so the point is within ``ε / 2`` of
    both declared distances.
    """
    gap = d - (ra + rb)
    if abs(gap) <= TANGENCY_TOLERANCE_M:
        return ra + gap / 2.0
    gap = d - abs(ra - rb)
    if abs(gap) > TANGENCY_TOLERANCE_M:
        return None
    return ra + gap / 2.0 if ra >= rb else -(ra - gap / 2.0)


def circle_intersections(
    a: Sequence[float], ra: float, b: Sequence[float], rb: float
) -> CircleIntersection:
    """Points at distance ``ra`` from ``a`` and ``rb`` from ``b`` (0, 1 or 2 of them).

    Raises ``ValueError`` only for non-finite centres or negative or non-finite radii.
    """
    pa, pb = _finite_point(a, "a"), _finite_point(b, "b")
    ra, rb = _finite(ra, "ra"), _finite(rb, "rb")
    d = math.dist(pa, pb)
    if d <= TANGENCY_TOLERANCE_M:
        return CircleIntersection("concentric", ())
    if d > ra + rb + TANGENCY_TOLERANCE_M:
        return CircleIntersection("disjoint", ())
    if d < abs(ra - rb) - TANGENCY_TOLERANCE_M:
        return CircleIntersection("contained", ())
    ux, uy = (pb[0] - pa[0]) / d, (pb[1] - pa[1]) / d
    tangent_at = _tangent_position(d, ra, rb)
    if tangent_at is not None:
        return CircleIntersection("tangent", ((pa[0] + tangent_at * ux, pa[1] + tangent_at * uy),))
    # Foot of the chord on the line A→B, measured from A.
    x = (d * d + (ra - rb) * (ra + rb)) / (2.0 * d)
    foot = (pa[0] + x * ux, pa[1] + x * uy)
    # Half chord from the factored (Heron) form: ra² − x² cancels badly for thin triangles.
    product = (ra + rb + d) * (rb - ra + d) * (ra - rb + d) * (ra + rb - d)
    h = math.sqrt(max(product, 0.0)) / (2.0 * d)
    left = (foot[0] - h * uy, foot[1] + h * ux)
    right = (foot[0] + h * uy, foot[1] - h * ux)
    return CircleIntersection("two", (left, right))


def triangulate(
    a: Sequence[float],
    b: Sequence[float],
    d_a: float,
    d_b: float,
    solution: Solution = "nearest",
    hint: Sequence[float] | None = None,
) -> Triangulation:
    """Fix P by its distances ``d_a`` to A and ``d_b`` to B (spec section 11.3).

    ``nearest`` is the candidate closest to ``hint`` (the drawn position of P), ``alt`` the
    other one; ties and a missing hint pick the left candidate as nearest. With a single
    (tangent) solution both choices give it. Distances must be positive.
    """
    _finite(d_a, "d_a", positive=True)
    _finite(d_b, "d_b", positive=True)
    intersection = circle_intersections(a, d_a, b, d_b)
    points = intersection.points
    if not points:
        return Triangulation(intersection, None, None)
    nearest = 0
    if hint is not None and len(points) == 2:
        h = _finite_point(hint, "hint")
        nearest = 1 if math.dist(points[1], h) < math.dist(points[0], h) else 0
    index = nearest if solution == "nearest" or len(points) == 1 else 1 - nearest
    return Triangulation(intersection, nearest, points[index])


def baseline_offset_point(
    a: Sequence[float], b: Sequence[float], s: float, d: float, side: Side
) -> Point:
    """``A + s·û + d·n̂``: ``s`` along A→B (may be negative), ``d ≥ 0`` to ``side``.

    ``left`` is the counter-clockwise normal of A→B in Y-up world coordinates. Raises
    :class:`DegenerateGeometryError` when A and B coincide.
    """
    pa, pb = _finite_point(a, "a"), _finite_point(b, "b")
    if not math.isfinite(s):
        raise ValueError(f"s must be finite, got {s}")
    d = _finite(d, "d")
    ux, uy, _ = _unit(pa, pb, "baseline")
    nx, ny = (-uy, ux) if side == "left" else (uy, -ux)
    return (pa[0] + s * ux + d * nx, pa[1] + s * uy + d * ny)


def scale_from_anchor(
    anchor: Sequence[float], point: Sequence[float], target_length: float
) -> Point:
    """``point`` moved along the ray anchor→point so that its distance is ``target_length``.

    Raises :class:`DegenerateGeometryError` for a zero-length segment (no direction) and
    ``ValueError`` for a non-positive or non-finite length.
    """
    pa, pp = _finite_point(anchor, "anchor"), _finite_point(point, "point")
    length = _finite(target_length, "target_length", positive=True)
    ux, uy, _ = _unit(pa, pp, "segment")
    return (pa[0] + length * ux, pa[1] + length * uy)


def fix_polyline_length(
    vertices: Sequence[Sequence[float]], anchor_index: int, free_index: int, target_length: float
) -> list[Point]:
    """Stretch the segment between two consecutive vertices of an open polyline.

    The free vertex moves to :func:`scale_from_anchor` and every vertex on its side of the
    anchor moves rigidly by the same vector (spec section 11.3); the others stay.
    """
    points = [_finite_point(v, "vertex") for v in vertices]
    in_range = 0 <= anchor_index < len(points) and 0 <= free_index < len(points)
    if not in_range or abs(anchor_index - free_index) != 1:
        raise ValueError("anchor and free vertex must be consecutive vertices of the polyline")
    old = points[free_index]
    new = scale_from_anchor(points[anchor_index], old, target_length)
    dx, dy = new[0] - old[0], new[1] - old[1]
    moved = range(free_index, len(points)) if free_index > anchor_index else range(free_index + 1)
    for i in moved:
        points[i] = (points[i][0] + dx, points[i][1] + dy)
    points[free_index] = new
    return points


def parse_distance(text: str) -> float:
    """Metres from user text such as ``"3,50"``, ``" 3.5 "`` or ``"12"`` (V-04).

    One decimal comma or point; no sign other than ``+``, no exponent, no thousands
    separator. Raises ``ValueError(DISTANCE_MESSAGE)`` for anything that is not a finite
    number greater than zero.
    """
    candidate = text.strip()
    if not _DISTANCE_PATTERN.fullmatch(candidate):
        raise ValueError(DISTANCE_MESSAGE)
    value = float(candidate.replace(",", "."))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(DISTANCE_MESSAGE)
    return value
