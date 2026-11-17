"""The ``fix_length`` edit and the declared segment lengths of lines and polylines.

Spec section 11.3 (REQ-06, US-07): fixing the length of segment AB with anchor A keeps
the direction A→B, moves B to the declared value and moves rigidly, by the same vector,
every vertex on B's side of the polyline (:func:`~sketch2cad.geometry.metrics.fix_polyline_length`).

Segments are numbered by their first vertex: segment ``k`` joins vertices ``k`` and
``k + 1``; a closed polyline adds segment ``n - 1`` joining the last vertex to vertex 0.
A line has the single segment 0. The fixed segment is the one between ``anchor_vertex``
and ``free_vertex``, which must be adjacent; without ``free_vertex`` it is the next
vertex (``anchor_vertex + 1``, wrapping on a closed polyline), or the previous one when
the anchor is the last vertex of an open polyline. On the closing segment of a closed
polyline the vertex list is rotated so that the segment is ``(n - 2, n - 1)``.

Declared lengths live in ``entity.attributes["declared_segments"]`` as ``{"k": value_m}``;
the key is managed by the server only. A declaration stays valid while the segment's
length equals its declared value within :data:`SEGMENT_TOLERANCE_M`; a geometry edit that
changes that length, or the number of vertices, drops it (:func:`retained_attributes`).
Spec section 8.2: a line or polyline is ``declared_dimension`` only when every segment
has a declared length; with only some of them it is ``trace_only``.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

from sketch2cad.db.models import MetricOrigin
from sketch2cad.geometry.metrics import (
    DISTANCE_MESSAGE,
    TANGENCY_TOLERANCE_M,
    DegenerateGeometryError,
    fix_polyline_length,
    parse_distance,
)

DECLARED_SEGMENTS: Final = "declared_segments"
"""Attribute holding the declared segment lengths; written only by ``fix_length``."""

LENGTH_CONFLICTS: Final = "length_conflicts"
"""Server-managed attribute: declarations a ``fix_length`` elsewhere broke, as
``{"k": {"declared_m": v, "actual_m": w}}``; kept until the entity's geometry is edited."""

SEGMENT_TOLERANCE_M: Final = TANGENCY_TOLERANCE_M
"""A declared segment stays declared while its length is within this many metres."""

FIXABLE_KINDS: Final = frozenset({"line", "polyline"})


class FixLengthError(ValueError):
    """The ``fix_length`` request cannot be applied (422); ``code`` names the V-xx rule."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FixedLength:
    """Result of :func:`fix_length`: the new geometry and attributes, and the segment."""

    geometry: dict[str, Any]
    attributes: dict[str, Any]
    segment: int
    anchor_vertex: int
    free_vertex: int


def declared_value(value: float | str) -> float:
    """Metres of the declared value: a JSON number or text with decimal comma or point.

    Raises :class:`FixLengthError` with ``code`` ``V-04`` unless it is finite and > 0.
    """
    if isinstance(value, str):
        try:
            return parse_distance(value)
        except ValueError:
            raise FixLengthError(DISTANCE_MESSAGE, code="V-04") from None
    if not math.isfinite(value) or value <= 0:
        raise FixLengthError(DISTANCE_MESSAGE, code="V-04")
    return float(value)


def _coords(geometry: Mapping[str, Any]) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in geometry["coords_m"]]


def _closed(geometry: Mapping[str, Any]) -> bool:
    return bool(geometry.get("closed", False))


def segment_count(geometry: Mapping[str, Any]) -> int:
    """Number of segments of a line or polyline geometry (0 for other kinds)."""
    if geometry.get("kind") not in FIXABLE_KINDS:
        return 0
    n = len(geometry["coords_m"])
    return n if _closed(geometry) else n - 1


def segment_length(geometry: Mapping[str, Any], segment: int) -> float:
    coords = _coords(geometry)
    return math.dist(coords[segment], coords[(segment + 1) % len(coords)])


def declared_segments(attributes: Mapping[str, Any]) -> dict[int, float]:
    """Declared lengths by segment index; anything malformed counts as undeclared."""
    raw: object = attributes.get(DECLARED_SEGMENTS)
    if not isinstance(raw, dict):
        return {}
    result: dict[int, float] = {}
    items: dict[object, object] = cast(dict[object, object], raw)
    for key, value in items.items():
        name = str(key)
        if name.isdigit() and isinstance(value, int | float) and not isinstance(value, bool):
            result[int(name)] = float(value)
    return result


def _with_declared(attributes: Mapping[str, Any], declared: Mapping[int, float]) -> dict[str, Any]:
    result = {k: v for k, v in attributes.items() if k != DECLARED_SEGMENTS}
    if declared:
        result[DECLARED_SEGMENTS] = {str(k): declared[k] for k in sorted(declared)}
    return result


def _segment_of(n: int, closed: bool, anchor: int, free: int) -> int | None:
    if abs(anchor - free) == 1:
        return min(anchor, free)
    if closed and n > 2 and {anchor, free} == {0, n - 1}:
        return n - 1
    return None


def resolve_segment(
    geometry: Mapping[str, Any], anchor: int, free: int | None
) -> tuple[int, int, int]:
    """``(anchor, free, segment)`` of the fixed segment; raises :class:`FixLengthError`."""
    if geometry.get("kind") not in FIXABLE_KINDS:
        raise FixLengthError("fix_length applies to lines and polylines only.")
    n, closed = len(geometry["coords_m"]), _closed(geometry)
    if not 0 <= anchor < n:
        raise FixLengthError(f"anchor_vertex must be a vertex index from 0 to {n - 1}.")
    if free is None:
        free = (anchor + 1) % n if closed else (anchor + 1 if anchor + 1 < n else anchor - 1)
    segment = _segment_of(n, closed, anchor, free) if 0 <= free < n else None
    if segment is None:
        raise FixLengthError("free_vertex must be a vertex adjacent to anchor_vertex.")
    return anchor, free, segment


def _stretch(
    coords: list[tuple[float, float]], anchor: int, free: int, value: float
) -> list[tuple[float, float]]:
    n = len(coords)
    if abs(anchor - free) == 1:
        return fix_polyline_length(coords, anchor, free, value)
    # Closing segment of a closed polyline: rotate so that it becomes (n - 2, n - 1).
    rotated = coords[1:] + coords[:1]
    stretched = fix_polyline_length(rotated, (anchor - 1) % n, (free - 1) % n, value)
    return stretched[-1:] + stretched[:-1]


def retained_attributes(
    old_geometry: Mapping[str, Any], new_geometry: Mapping[str, Any], attributes: Mapping[str, Any]
) -> dict[str, Any]:
    """``attributes`` keeping only the declarations still true for ``new_geometry``.

    All are dropped when the kind, the vertex count or ``closed`` changes (segments are
    renumbered); otherwise a segment keeps its declaration while its length stays within
    :data:`SEGMENT_TOLERANCE_M` of the declared value.
    """
    declared = declared_segments(attributes)
    same_shape = (
        segment_count(new_geometry) > 0
        and old_geometry.get("kind") == new_geometry.get("kind")
        and len(old_geometry.get("coords_m", [])) == len(new_geometry["coords_m"])
        and _closed(old_geometry) == _closed(new_geometry)
    )
    kept = {
        k: v
        for k, v in declared.items()
        if same_shape
        and k < segment_count(new_geometry)
        and abs(segment_length(new_geometry, k) - v) <= SEGMENT_TOLERANCE_M
    }
    return _with_declared(attributes, kept)


def fix_length(
    geometry: Mapping[str, Any],
    attributes: Mapping[str, Any],
    anchor_vertex: int,
    free_vertex: int | None,
    value_m: float | str,
) -> FixedLength:
    """Stretch the segment to ``value_m`` and record its declaration (spec section 11.3).

    Other declarations whose length changed are dropped (:func:`retained_attributes`).
    Raises :class:`FixLengthError` (``V-04`` for the value) without changing anything.
    """
    value = declared_value(value_m)
    anchor, free, segment = resolve_segment(geometry, anchor_vertex, free_vertex)
    try:
        coords = _stretch(_coords(geometry), anchor, free, value)
    except DegenerateGeometryError:
        raise FixLengthError("The segment has zero length, so it has no direction.") from None
    new_geometry = {**geometry, "coords_m": [[x, y] for x, y in coords]}
    kept = declared_segments(retained_attributes(geometry, new_geometry, attributes))
    kept[segment] = value
    return FixedLength(new_geometry, _with_declared(attributes, kept), segment, anchor, free)


@dataclass(frozen=True)
class BrokenDeclaration:
    """A declared segment length that no longer holds after an edit."""

    segment: int
    declared_m: float
    actual_m: float | None
    """Length now, or ``None`` when the segments were renumbered."""


def broken_declarations(
    before: Mapping[str, Any], after: Mapping[str, Any], geometry: Mapping[str, Any]
) -> list[BrokenDeclaration]:
    """Declarations in attributes ``before`` that attributes ``after`` dropped."""
    kept = declared_segments(after)
    count = segment_count(geometry)
    return [
        BrokenDeclaration(k, v, segment_length(geometry, k) if k < count else None)
        for k, v in sorted(declared_segments(before).items())
        if k not in kept
    ]


def with_length_conflicts(
    attributes: Mapping[str, Any], broken: list[BrokenDeclaration]
) -> dict[str, Any]:
    """``attributes`` recording ``broken`` in :data:`LENGTH_CONFLICTS` (merged by segment)."""
    raw: object = attributes.get(LENGTH_CONFLICTS)
    current: dict[str, Any] = dict(cast(dict[str, Any], raw)) if isinstance(raw, dict) else {}
    for item in broken:
        current[str(item.segment)] = {"declared_m": item.declared_m, "actual_m": item.actual_m}
    return {**attributes, LENGTH_CONFLICTS: current}


def declared_origin(geometry: Mapping[str, Any], attributes: Mapping[str, Any]) -> str | None:
    """``declared_dimension`` when every segment is declared, ``trace_only`` when only some
    are, ``None`` when none is (the caller keeps its own default)."""
    count = segment_count(geometry)
    declared = [k for k in declared_segments(attributes) if k < count]
    if not declared:
        return None
    if len(declared) == count:
        return MetricOrigin.DECLARED_DIMENSION.value
    return MetricOrigin.TRACE_ONLY.value
