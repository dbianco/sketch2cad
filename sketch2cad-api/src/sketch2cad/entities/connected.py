"""Connected vertices that follow a ``fix_length`` (walkthrough finding: corners must stay closed).

``fix_length`` moves vertices of one line or polyline (:mod:`sketch2cad.entities.ops_fix_length`).
Walls drawn as separate entities meet at shared corners or end on another wall (T
junctions); before this module they stayed behind and the corners opened. :func:`propagate`
moves them too, in rounds until nothing new moves:

- **Coincident vertex**: a vertex of another line, polyline or dimension within
  ``tolerance`` of a vertex that was decided earlier (the fixed entity's, or one moved in an
  earlier round) moves by the same vector, as one rigid corner. When several are within
  ``tolerance`` the nearest wins, ties to the earliest decided.
- **T junction**: an *end* vertex (line ends, open polyline ends, dimension points) within
  ``tolerance`` of the interior of a segment whose ends moved slides with it: at the
  segment parameter ``t`` it moves by ``(1 - t)·dA + t·dB``, so it stays on the segment at
  the same proportion (a rigidly moved wall carries it by the same vector).

Matching always uses the positions before the edit; entities are visited in the caller's
order (by id), so the result is deterministic. A T junction takes the vectors its host
segment's ends have when it is reached (an end still undecided counts as staying).
A vertex on a ``locked`` entity is not moved: it is reported in ``pinned`` with the gap
left open, and it then holds its neighbours still like an unmoved vertex. Vertices of the
fixed entity are never changed here. Pure functions, no database.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sketch2cad.entities.dimensions import measured_length
from sketch2cad.geometry.homography import Point

CONNECTED_TOLERANCE_PAPER_MM: Final = 1.0
"""Paper millimetres within which two vertices count as one corner (and an end lies on a
wall). There is no server-side snapping tolerance: the editor's endpoint snap is 8 screen
px and copies the coordinates exactly."""
DEFAULT_PAPER_SCALE: Final = 100
"""Paper scale used without a declared one; same value as
``sketch2cad.plans.local_frame.PROVISIONAL_PAPER_SCALE`` (spec section 10)."""
PROPAGATED_KINDS: Final = frozenset({"line", "polyline", "dimension"})
"""Geometry kinds whose vertices follow a moved corner."""


def connected_tolerance_m(paper_scale: int | None) -> float:
    """Metres equivalent to :data:`CONNECTED_TOLERANCE_PAPER_MM` at ``paper_scale``."""
    return CONNECTED_TOLERANCE_PAPER_MM * (paper_scale or DEFAULT_PAPER_SCALE) / 1000.0


@dataclass(frozen=True)
class Neighbour:
    """Another live entity of the revision that may follow the edit."""

    id: str
    geometry: Mapping[str, Any]
    locked: bool = False


@dataclass(frozen=True)
class VertexMove:
    entity_id: str
    vertex: int
    old: Point
    new: Point


@dataclass(frozen=True)
class PinnedVertex:
    """A vertex on a locked layer that should have moved; ``gap_m`` is left open."""

    entity_id: str
    vertex: int
    gap_m: float


@dataclass(frozen=True)
class Propagation:
    """New geometries of the neighbours that moved, their moved vertices and pinned ones."""

    geometries: dict[str, dict[str, Any]]
    moves: tuple[VertexMove, ...]
    pinned: tuple[PinnedVertex, ...]


def vertices(geometry: Mapping[str, Any]) -> list[Point]:
    """Vertices of a line, polyline or dimension (``p1_m``, ``p2_m``); none for other kinds."""
    kind = geometry.get("kind")
    if kind in ("line", "polyline"):
        return [(float(x), float(y)) for x, y in geometry["coords_m"]]
    if kind == "dimension":
        return [_point(geometry["p1_m"]), _point(geometry["p2_m"])]
    return []


def _point(value: Sequence[float]) -> Point:
    return (float(value[0]), float(value[1]))


def with_vertices(geometry: Mapping[str, Any], points: Sequence[Point]) -> dict[str, Any]:
    """Copy of ``geometry`` with new vertices; a dimension's measured value follows them."""
    if geometry.get("kind") == "dimension":
        p1, p2 = [list(points[0]), list(points[1])]
        return {**geometry, "p1_m": p1, "p2_m": p2, "measured_value_m": measured_length(p1, p2)}
    return {**geometry, "coords_m": [[x, y] for x, y in points]}


def _ends(geometry: Mapping[str, Any], count: int) -> tuple[int, ...]:
    if geometry.get("kind") == "polyline" and geometry.get("closed", False):
        return ()
    return (0, count - 1) if count >= 2 else ()


def _segments(geometry: Mapping[str, Any], count: int) -> list[tuple[int, int]]:
    if geometry.get("kind") not in ("line", "polyline"):
        return []
    pairs = [(i, i + 1) for i in range(count - 1)]
    if geometry.get("kind") == "polyline" and geometry.get("closed", False) and count > 2:
        pairs.append((count - 1, 0))
    return pairs


@dataclass
class _Entity:
    id: str
    geometry: Mapping[str, Any]
    old: list[Point]
    locked: bool
    deltas: dict[int, Point]
    """Decided vertices and their displacement (zero for a held vertex)."""
    new: dict[int, Point]
    """New position of each decided vertex."""


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _is_zero(d: Point) -> bool:
    return d == (0.0, 0.0)


def _segment_parameter(p: Point, a: Point, b: Point) -> tuple[float, float]:
    """``(t, distance)`` of the projection of ``p`` on the segment ``a``-``b``."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 == 0.0:
        return 0.0, math.dist(p, a)
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2
    return t, abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / math.sqrt(length2)


def _corner_delta(
    p: Point, decided: list[tuple[_Entity, int]], tolerance: float
) -> tuple[Point, Point] | None:
    """``(delta, exact new position)`` of the nearest decided vertex within ``tolerance``."""
    best: tuple[float, _Entity, int] | None = None
    for entity, index in decided:
        distance = math.dist(p, entity.old[index])
        if distance <= tolerance and (best is None or distance < best[0]):
            best = (distance, entity, index)
    if best is None:
        return None
    distance, entity, index = best
    delta = entity.deltas[index]
    # Exactly coincident vertices end exactly coincident (no float drift between them).
    if distance == 0.0:
        return delta, entity.new[index]
    return delta, (p[0] + delta[0], p[1] + delta[1])


def _slide_delta(
    p: Point, owner: str, hosts: list[_Entity], tolerance: float
) -> tuple[Point, Point] | None:
    """Delta of an end lying on the interior of a moved segment (T junction), if any."""
    best: tuple[float, Point] | None = None
    for host in hosts:
        if host.id == owner:
            continue
        for i, j in _segments(host.geometry, len(host.old)):
            da, db = host.deltas.get(i, (0.0, 0.0)), host.deltas.get(j, (0.0, 0.0))
            if _is_zero(da) and _is_zero(db):
                continue
            a, b = host.old[i], host.old[j]
            t, distance = _segment_parameter(p, a, b)
            interior = math.dist(p, a) > tolerance and math.dist(p, b) > tolerance
            if interior and 0.0 < t < 1.0 and distance <= tolerance:
                if best is None or distance < best[0]:
                    delta = ((1 - t) * da[0] + t * db[0], (1 - t) * da[1] + t * db[1])
                    best = (distance, delta)
    if best is None:
        return None
    delta = best[1]
    return delta, (p[0] + delta[0], p[1] + delta[1])


def propagate(
    source_id: str,
    old_geometry: Mapping[str, Any],
    new_geometry: Mapping[str, Any],
    neighbours: Sequence[Neighbour],
    tolerance: float,
) -> Propagation:
    """Move the vertices connected to the ones ``old_geometry`` → ``new_geometry`` moved.

    ``neighbours`` are the other live entities (any kind; only lines, polylines and
    dimensions follow), in a stable order. See the module docstring for the rules.
    """
    old, new = vertices(old_geometry), vertices(new_geometry)
    deltas = {i: _sub(new[i], old[i]) for i in range(len(old))}
    source = _Entity(source_id, old_geometry, old, False, deltas, dict(enumerate(new)))
    others = [
        _Entity(n.id, n.geometry, vertices(n.geometry), n.locked, {}, {})
        for n in neighbours
        if n.id != source_id and n.geometry.get("kind") in PROPAGATED_KINDS
    ]
    pinned: list[PinnedVertex] = []
    # Decided vertices in decision order; held ones (zero vector) keep their neighbours still.
    decided = [(source, i) for i in range(len(old))]
    if all(_is_zero(d) for d in source.deltas.values()):
        return Propagation({}, (), ())
    while True:
        hosts = [e for e in (source, *others) if any(not _is_zero(d) for d in e.deltas.values())]
        found: list[tuple[_Entity, int, Point, Point]] = []
        for entity in others:
            ends = _ends(entity.geometry, len(entity.old))
            for index, point in enumerate(entity.old):
                if index in entity.deltas:
                    continue
                hit = _corner_delta(point, decided, tolerance)
                if hit is None and index in ends:
                    hit = _slide_delta(point, entity.id, hosts, tolerance)
                if hit is not None:
                    found.append((entity, index, *hit))
        if not found:
            break
        for entity, index, delta, target in found:
            if entity.locked and not _is_zero(delta):
                pinned.append(PinnedVertex(entity.id, index, math.hypot(*delta)))
                delta, target = (0.0, 0.0), entity.old[index]
            entity.deltas[index], entity.new[index] = delta, target
            decided.append((entity, index))
    return _result(others, pinned)


def _result(others: list[_Entity], pinned: list[PinnedVertex]) -> Propagation:
    geometries: dict[str, dict[str, Any]] = {}
    moves: list[VertexMove] = []
    for entity in others:
        points = list(entity.old)
        for index in sorted(entity.new):
            target = entity.new[index]
            if target != entity.old[index]:
                points[index] = target
                moves.append(VertexMove(entity.id, index, entity.old[index], target))
        if points != entity.old:
            geometries[entity.id] = with_vertices(entity.geometry, points)
    return Propagation(geometries, tuple(moves), tuple(pinned))
