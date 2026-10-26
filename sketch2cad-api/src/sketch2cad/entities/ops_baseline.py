"""The ``baseline_offset`` edit: fix a point by rectangular coordinates (US-09).

Spec section 11.3: ``P = A + s·û + d·n̂`` where ``û`` is the unit direction from base point
A to base point B, ``s`` the distance along it (negative is behind A), ``d >= 0`` the
perpendicular offset and ``n̂`` the left (counter-clockwise, Y up) or right normal. Pure
function: the caller resolves and checks the references (V-07).
"""

from dataclasses import dataclass
from typing import Literal

from sketch2cad.entities.constraints import (
    BaselineOffsetConstraint,
    BaselineOffsetParams,
    ConstraintOperationError,
    signed_value,
)
from sketch2cad.entities.schemas import Reference
from sketch2cad.geometry.homography import Point
from sketch2cad.geometry.metrics import DegenerateGeometryError, baseline_offset_point


@dataclass(frozen=True)
class BaselinePlaced:
    """The computed position and the constraint to store."""

    point: Point
    constraint: BaselineOffsetConstraint


def baseline_offset_position(
    base_a: tuple[Reference, Point],
    base_b: tuple[Reference, Point],
    s_m: float | str,
    d_m: float | str,
    side: Literal["left", "right"],
) -> BaselinePlaced:
    """Position of P from the baseline ``base_a`` → ``base_b`` (``(reference, position)``).

    Raises :class:`ConstraintOperationError` for a value that is not a finite number
    (``d_m`` must also be >= 0) or when the base points coincide (no direction).
    """
    (id_a, a), (id_b, b) = base_a, base_b
    s = signed_value(s_m, "s_m")
    d = signed_value(d_m, "d_m", non_negative=True)
    try:
        point = baseline_offset_point(a, b, s, d, side)
    except DegenerateGeometryError:
        raise ConstraintOperationError(
            "The two base points are at the same position, so the baseline has no direction."
        ) from None
    constraint = BaselineOffsetConstraint(
        kind="baseline_offset",
        refs=[id_a, id_b],
        params=BaselineOffsetParams(s_m=s, d_m=d, side=side),
    )
    return BaselinePlaced(point, constraint)
