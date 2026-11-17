"""Shapes of ``entity.constraint`` (spec sections 8.2 and 11.3).

An entity fixed by triangulation or by baseline plus offset keeps the definition it was
computed from, so it can be recomputed when a reference moves::

    {"kind": "triangulation", "refs": [A, B],
     "params": {"d_a_m": 2.10, "d_b_m": 3.45, "solution": "nearest", "side": "left"}}
    {"kind": "baseline_offset", "refs": [A, B],
     "params": {"s_m": 1.50, "d_m": 0.80, "side": "left"}}

``refs`` are entity ids of points or symbols, or vertex references
``{"entity_id": W, "vertex": 0}`` to a corner of a ``line`` or ``polyline`` (PoC
assumption D-15, 2026-10-26, to revisit with real sketches); constraints stored with plain
ids keep loading. For a baseline they are the base points A and B in order, which fixes the
direction of the baseline and so the meaning of ``side``. The database only
checks ``kind`` (``ck_entity_constraint_kind``); everything else is validated here.

The module also holds what the ``triangulate`` and ``baseline_offset`` edits share: the
position of a point-like entity (a ``point`` or the anchor of a ``symbol``) or of a vertex
reference, the V-07
message and the parsing of the lengths typed by the user.
"""

import math
import re
import uuid
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from sketch2cad.entities.schemas import Number, PositiveNumber, Reference, VertexRef
from sketch2cad.geometry.homography import Point
from sketch2cad.geometry.metrics import DISTANCE_MESSAGE, parse_distance

V07_MESSAGE: Final = "Esta referencia no es métrica o depende del punto que intentas fijar."
"""User-facing message of V-07 (PRD): a reference is not metric or depends on the target."""

_SIGNED_NUMBER: Final = re.compile(r"[+-]?(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)", re.ASCII)

NonNegativeNumber = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0)]
References = Annotated[list[Reference], Field(min_length=2, max_length=2)]
VERTEX_KINDS: Final = frozenset({"line", "polyline"})
"""Geometry kinds whose vertices may serve as references (:class:`VertexRef`)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ConstraintBase(_Strict):
    refs: References

    @model_validator(mode="after")
    def _distinct_references(self) -> "_ConstraintBase":
        if self.refs[0] == self.refs[1]:
            raise ValueError("the two references of a constraint must be different")
        return self


class TriangulationParams(_Strict):
    """Distances from the fixed point to references A and B and the chosen solution.

    ``solution`` is what the user asked for, relative to the position of P at that time.
    ``side`` is the candidate actually applied, to the ``left`` or ``right`` of A→B, so a
    recompute keeps P on the same side (:mod:`sketch2cad.entities.recompute`); it is left
    out when the circles were tangent and in constraints stored before it existed.
    """

    d_a_m: PositiveNumber
    d_b_m: PositiveNumber
    solution: Literal["nearest", "alt"]
    side: Literal["left", "right"] | None = None


class TriangulationConstraint(_ConstraintBase):
    kind: Literal["triangulation"]
    params: TriangulationParams


class BaselineOffsetParams(_Strict):
    """``P = A + s·û + d·n̂``: distance along the baseline, perpendicular offset and side."""

    s_m: Number
    d_m: NonNegativeNumber
    side: Literal["left", "right"]


class BaselineOffsetConstraint(_ConstraintBase):
    kind: Literal["baseline_offset"]
    params: BaselineOffsetParams


Constraint = Annotated[
    TriangulationConstraint | BaselineOffsetConstraint, Field(discriminator="kind")
]

CONSTRAINT_ADAPTER: TypeAdapter[TriangulationConstraint | BaselineOffsetConstraint] = TypeAdapter(
    Constraint
)


def parse_constraint(value: Any) -> TriangulationConstraint | BaselineOffsetConstraint:
    """Validate a stored or requested ``entity.constraint`` value; raises ``ValidationError``."""
    return CONSTRAINT_ADAPTER.validate_python(value)


def constraint_to_json(
    constraint: TriangulationConstraint | BaselineOffsetConstraint,
) -> dict[str, Any]:
    """JSON-ready dict for the ``entity.constraint`` column; a null ``side`` is left out."""
    return constraint.model_dump(mode="json", exclude_none=True)


class ConstraintOperationError(ValueError):
    """A constraint edit cannot be applied (422); ``code`` names the V-xx rule."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def point_position(geometry: Mapping[str, Any]) -> Point | None:
    """World position of a ``point`` or of a ``symbol`` anchor; ``None`` for other kinds."""
    kind = geometry.get("kind")
    if kind == "point":
        x, y = geometry["coords_m"][0]
    elif kind == "symbol":
        x, y = geometry["anchor_m"]
    else:
        return None
    return (float(x), float(y))


def reference_entity_id(ref: Reference) -> uuid.UUID:
    """The entity a reference points at: the id itself or the vertex's line or polyline."""
    return ref.entity_id if isinstance(ref, VertexRef) else ref


def stored_reference_ids(constraint: Any) -> list[uuid.UUID]:
    """Entity ids of the ``refs`` of a stored ``entity.constraint`` (either form).

    Lenient: malformed items are skipped, so a damaged row cannot break the graph.
    """
    if not isinstance(constraint, Mapping):
        return []
    refs: object = cast(Mapping[str, object], constraint).get("refs")
    ids: list[uuid.UUID] = []
    for ref in cast(list[object], refs) if isinstance(refs, list) else []:
        raw = cast(Mapping[str, object], ref).get("entity_id") if isinstance(ref, Mapping) else ref
        try:
            ids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    return ids


class VertexOutOfRangeError(LookupError):
    """A vertex reference names a vertex its line or polyline does not have."""


def reference_position(ref: Reference, geometry: Mapping[str, Any]) -> Point | None:
    """Position of ``ref`` in the referenced entity's ``geometry``.

    ``None`` when the kind cannot serve: a plain id needs a point or symbol, a vertex
    reference a line or polyline. Raises :class:`VertexOutOfRangeError` for a vertex index
    the geometry lacks.
    """
    if not isinstance(ref, VertexRef):
        return point_position(geometry)
    if geometry.get("kind") not in VERTEX_KINDS:
        return None
    coords = geometry["coords_m"]
    if ref.vertex >= len(coords):
        raise VertexOutOfRangeError(ref.vertex)
    x, y = coords[ref.vertex]
    return (float(x), float(y))


def with_position(geometry: Mapping[str, Any], point: Point) -> dict[str, Any]:
    """Copy of a point or symbol ``geometry`` placed at ``point``."""
    if geometry.get("kind") == "point":
        return {**geometry, "coords_m": [[point[0], point[1]]]}
    return {**geometry, "anchor_m": [point[0], point[1]]}


def distance_value(value: float | str) -> float:
    """A distance greater than zero, as a number or text with decimal comma (V-04)."""
    if isinstance(value, str):
        try:
            return parse_distance(value)
        except ValueError:
            raise ConstraintOperationError(DISTANCE_MESSAGE, code="V-04") from None
    if not math.isfinite(value) or value <= 0:
        raise ConstraintOperationError(DISTANCE_MESSAGE, code="V-04")
    return float(value)


def signed_value(value: float | str, name: str, *, non_negative: bool = False) -> float:
    """A finite length of any sign (or ``>= 0`` with ``non_negative``), number or text."""
    if isinstance(value, str):
        text = value.strip()
        if not _SIGNED_NUMBER.fullmatch(text):
            raise ConstraintOperationError(f"{name} must be a number such as 1,50.")
        value = float(text.replace(",", "."))
    if not math.isfinite(value):
        raise ConstraintOperationError(f"{name} must be a finite number.")
    if non_negative and value < 0:
        raise ConstraintOperationError(f"{name} must be zero or greater.")
    return float(value) + 0.0  # normalise -0.0
