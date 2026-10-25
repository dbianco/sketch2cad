"""The ``triangulate`` edit: fix a point by its distances to two references (US-08, V-06).

Spec section 11.3: P lies at ``d_a`` from reference A and ``d_b`` from reference B, on the
intersection of two circles. There are 0, 1 (tangent) or 2 candidates; ``nearest`` picks
the one closest to the current position of P and ``alt`` the other one. Circles that do
not cross (disjoint, one inside the other, concentric) are rejected with V-06 and nothing
changes. Pure function: the caller resolves and checks the references (V-07).
"""

import uuid
from dataclasses import dataclass
from typing import Final, Literal

from sketch2cad.entities.constraints import (
    ConstraintOperationError,
    TriangulationConstraint,
    TriangulationParams,
    distance_value,
)
from sketch2cad.geometry.homography import Point
from sketch2cad.geometry.metrics import triangulate

V06_MESSAGE: Final = (
    "Estas dos distancias no se cruzan. Revisa las medidas o las referencias elegidas."
)
"""User-facing message of V-06 (PRD)."""


@dataclass(frozen=True)
class Triangulated:
    """The applied position, every candidate, the index of the applied one and the constraint."""

    point: Point
    candidates: tuple[Point, ...]
    chosen: int
    constraint: TriangulationConstraint


def triangulate_position(
    position: Point,
    ref_a: tuple[uuid.UUID, Point],
    ref_b: tuple[uuid.UUID, Point],
    d_a_m: float | str,
    d_b_m: float | str,
    solution: Literal["nearest", "alt"] = "nearest",
) -> Triangulated:
    """Candidates for P (currently at ``position``) and the one picked by ``solution``.

    ``ref_a`` and ``ref_b`` are ``(entity id, position)``. Raises
    :class:`ConstraintOperationError` with ``code`` ``V-04`` for a distance that is not a
    number greater than zero and ``V-06`` when the circles do not cross.
    """
    (id_a, a), (id_b, b) = ref_a, ref_b
    d_a, d_b = distance_value(d_a_m), distance_value(d_b_m)
    result = triangulate(a, b, d_a, d_b, solution, hint=position)
    if result.point is None or result.nearest_index is None:
        raise ConstraintOperationError(V06_MESSAGE, code="V-06")
    candidates = result.candidates
    nearest = result.nearest_index
    chosen = nearest if solution == "nearest" or len(candidates) == 1 else 1 - nearest
    constraint = TriangulationConstraint(
        kind="triangulation",
        refs=[id_a, id_b],
        params=TriangulationParams(d_a_m=d_a, d_b_m=d_b, solution=solution),
    )
    return Triangulated(result.point, candidates, chosen, constraint)
