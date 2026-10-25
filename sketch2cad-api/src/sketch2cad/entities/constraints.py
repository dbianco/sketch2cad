"""Shapes of ``entity.constraint`` (spec sections 8.2 and 11.3).

An entity fixed by triangulation or by baseline plus offset keeps the definition it was
computed from, so it can be recomputed when a reference moves::

    {"kind": "triangulation", "refs": [A, B],
     "params": {"d_a_m": 2.10, "d_b_m": 3.45, "solution": "nearest"}}
    {"kind": "baseline_offset", "refs": [A, B],
     "params": {"s_m": 1.50, "d_m": 0.80, "side": "left"}}

``refs`` are entity ids. For a baseline they are the base points A and B in order, which
fixes the direction of the baseline and so the meaning of ``side``. The database only
checks ``kind`` (``ck_entity_constraint_kind``); everything else is validated here.
"""

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from sketch2cad.entities.schemas import Number, PositiveNumber

NonNegativeNumber = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0)]
References = Annotated[list[uuid.UUID], Field(min_length=2, max_length=2)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ConstraintBase(_Strict):
    refs: References

    @model_validator(mode="after")
    def _distinct_references(self) -> "_ConstraintBase":
        if self.refs[0] == self.refs[1]:
            raise ValueError("the two references of a constraint must be different entities")
        return self


class TriangulationParams(_Strict):
    """Distances from the fixed point to references A and B and the chosen solution."""

    d_a_m: PositiveNumber
    d_b_m: PositiveNumber
    solution: Literal["nearest", "alt"]


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
    """JSON-ready dict for the ``entity.constraint`` column."""
    return constraint.model_dump(mode="json")
