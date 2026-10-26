"""Request and response models of drawing entities (spec section 8.2).

Geometry is a union discriminated by ``kind``, which must equal the entity ``type``.
Coordinates are metres in the local frame, Y up. Numbers must be JSON numbers and finite;
strings are never coerced. ``dimension.measured_value_m`` is always recomputed from
``p1_m`` and ``p2_m``: the server is the authority on measurements.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

from sketch2cad.catalog.schema import ATTRIBUTE_NAME, LAYER_NAME, SYMBOL_ID
from sketch2cad.db.models import EntityType, MetricOrigin, ReviewStatus
from sketch2cad.entities.dimensions import measured_length

# One request carries at most the entity count the editor is sized for (spec section 17).
MAX_ENTITIES_PER_REQUEST = 2000

Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
PositiveNumber = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0)]
Coordinate = tuple[Number, Number]
LayerName = Annotated[str, StringConstraints(pattern=LAYER_NAME)]
AttributeName = Annotated[str, StringConstraints(pattern=ATTRIBUTE_NAME)]
AttributeValue = StrictStr | StrictInt | Number | StrictBool | None
Attributes = dict[AttributeName, AttributeValue]


class EntityOperation(StrEnum):
    """Edit operations of ``PATCH /entities/{id}``, recorded in ``entity.updated``."""

    MOVE_VERTEX = "move_vertex"
    SNAP_ORTHOGONAL = "snap_orthogonal"
    SNAP_ENDPOINT = "snap_endpoint"
    SNAP_GRID = "snap_grid"
    RELABEL = "relabel"
    RECLASSIFY = "reclassify"
    FIX_LENGTH = "fix_length"
    TRIANGULATE = "triangulate"
    BASELINE_OFFSET = "baseline_offset"


GEOMETRY_OPERATIONS = frozenset(
    {
        EntityOperation.MOVE_VERTEX,
        EntityOperation.SNAP_ORTHOGONAL,
        EntityOperation.SNAP_ENDPOINT,
        EntityOperation.SNAP_GRID,
    }
)


class SourceMethod(StrEnum):
    MANUAL = "manual"
    CV = "cv"
    OCR = "ocr"
    MODEL = "model"
    DERIVED = "derived"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineGeometry(_Strict):
    kind: Literal["line"]
    coords_m: Annotated[list[Coordinate], Field(min_length=2, max_length=2)]


class PolylineGeometry(_Strict):
    """Open polyline of at least 2 vertices; a closed one needs at least 3."""

    kind: Literal["polyline"]
    coords_m: Annotated[list[Coordinate], Field(min_length=2)]
    closed: StrictBool = False

    @model_validator(mode="after")
    def _closed_needs_three_vertices(self) -> "PolylineGeometry":
        if self.closed and len(self.coords_m) < 3:
            raise ValueError("a closed polyline needs at least 3 vertices")
        return self


class PointGeometry(_Strict):
    kind: Literal["point"]
    coords_m: Annotated[list[Coordinate], Field(min_length=1, max_length=1)]


class TextGeometry(_Strict):
    kind: Literal["text"]
    anchor_m: Coordinate
    rotation_deg: Number = 0.0
    height_mm_paper: PositiveNumber
    content: Annotated[StrictStr, StringConstraints(min_length=1)]


class DimensionGeometry(_Strict):
    """``declared_value_m`` is null for a plain measurement; ``measured_value_m`` is computed.

    A client-sent ``measured_value_m`` is ignored and replaced by the ``p1_m``-``p2_m``
    distance; ``declared_value_m`` is kept as sent (``sketch2cad.entities.dimensions``).
    """

    kind: Literal["dimension"]
    p1_m: Coordinate
    p2_m: Coordinate
    offset_m: Number = 0.0
    declared_value_m: PositiveNumber | None = None
    measured_value_m: Number | None = None

    @model_validator(mode="after")
    def _measure(self) -> "DimensionGeometry":
        self.measured_value_m = measured_length(self.p1_m, self.p2_m)
        return self


class SymbolGeometry(_Strict):
    """A catalog symbol; ``attributes`` holds its block attributes (e.g. ``evidence_number``)."""

    kind: Literal["symbol"]
    anchor_m: Coordinate
    rotation_deg: Number = 0.0
    scale: PositiveNumber = 1.0
    symbol_id: Annotated[str, StringConstraints(pattern=SYMBOL_ID)]
    attributes: Attributes = Field(default_factory=dict[str, AttributeValue])


Geometry = Annotated[
    LineGeometry
    | PolylineGeometry
    | PointGeometry
    | TextGeometry
    | DimensionGeometry
    | SymbolGeometry,
    Field(discriminator="kind"),
]


class EntityIn(_Strict):
    """A new entity drawn by hand; provenance, review and metric fields are set by the server.

    ``evidence_id`` places evidence of the plan's case file; only points and symbols may.
    """

    type: EntityType
    layer: LayerName
    geometry: Geometry
    attributes: Attributes = Field(default_factory=dict[str, AttributeValue])
    evidence_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _kind_matches_type(self) -> "EntityIn":
        if self.geometry.kind != self.type.value:
            raise ValueError("geometry.kind must equal type")
        return self


class EntitiesCreate(_Strict):
    entities: Annotated[list[EntityIn], Field(min_length=1, max_length=MAX_ENTITIES_PER_REQUEST)]


# Body fields of the server-computed operations: (required, optional) per operation.
OPERATION_FIELDS: dict[EntityOperation, tuple[tuple[str, ...], tuple[str, ...]]] = {
    EntityOperation.FIX_LENGTH: (("anchor_vertex", "value_m"), ("free_vertex",)),
    EntityOperation.TRIANGULATE: (("ref_a", "ref_b", "d_a_m", "d_b_m"), ("solution",)),
    EntityOperation.BASELINE_OFFSET: (("base_a", "base_b", "s_m", "d_m", "side"), ()),
}
LengthValue = Number | StrictStr
"""Metres as a JSON number or as text with decimal comma or point (V-04)."""


class EntityPatch(_Strict):
    """An edit. ``geometry`` replaces the whole geometry and keeps the entity's ``kind``.

    ``move_vertex`` and the ``snap_*`` operations need ``geometry``; ``reclassify`` needs
    ``layer``; ``relabel`` needs ``geometry``, ``attributes`` or ``evidence_id`` (evidence
    of the plan's case file, or ``null`` to drop the reference; only ``relabel`` takes
    it, and only for points and symbols). Moving a point or symbol
    that has a ``constraint`` needs ``confirm_constraint_removal = true`` (409
    ``constraint_removal_required`` otherwise); it removes the constraint and sets
    ``metric_origin`` to ``none`` (spec section 11.3).

    The other operations take only their own fields and the server computes the geometry:

    - ``fix_length`` (lines and polylines): ``anchor_vertex``, ``value_m`` (> 0, V-04) and
      the optional adjacent ``free_vertex`` (``sketch2cad.entities.ops_fix_length``).
    - ``triangulate`` (points and symbols): references ``ref_a`` and ``ref_b``, distances
      ``d_a_m`` and ``d_b_m`` (> 0, V-04) and ``solution`` (``nearest``, the default, is
      the candidate closest to the current position; ``alt`` is the other one).
    - ``baseline_offset`` (points and symbols): base points ``base_a`` and ``base_b``,
      ``s_m`` along A→B (any sign), perpendicular ``d_m`` (>= 0) and ``side``.
    """

    operation: EntityOperation
    geometry: Geometry | None = None
    attributes: Attributes | None = None
    evidence_id: uuid.UUID | None = None
    layer: LayerName | None = None
    anchor_vertex: Annotated[StrictInt, Field(ge=0)] | None = None
    free_vertex: Annotated[StrictInt, Field(ge=0)] | None = None
    value_m: LengthValue | None = None
    ref_a: uuid.UUID | None = None
    ref_b: uuid.UUID | None = None
    d_a_m: LengthValue | None = None
    d_b_m: LengthValue | None = None
    solution: Literal["nearest", "alt"] | None = None
    base_a: uuid.UUID | None = None
    base_b: uuid.UUID | None = None
    s_m: LengthValue | None = None
    d_m: LengthValue | None = None
    side: Literal["left", "right"] | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    confirm_constraint_removal: StrictBool = False

    @model_validator(mode="after")
    def _operation_has_its_fields(self) -> "EntityPatch":
        self._check_computed_fields()
        if self.operation in GEOMETRY_OPERATIONS and self.geometry is None:
            raise ValueError(f"{self.operation.value} needs geometry")
        if self.operation is EntityOperation.RECLASSIFY and self.layer is None:
            raise ValueError("reclassify needs layer")
        sets_evidence = self.sets_evidence
        if sets_evidence and self.operation is not EntityOperation.RELABEL:
            raise ValueError("evidence_id is for relabel only")
        if self.operation is EntityOperation.RELABEL and (
            self.geometry is None and self.attributes is None and not sets_evidence
        ):
            raise ValueError("relabel needs geometry, attributes or evidence_id")
        return self

    @property
    def sets_evidence(self) -> bool:
        """Whether the body sends ``evidence_id``, including an explicit ``null``."""
        return "evidence_id" in self.model_fields_set

    def _check_computed_fields(self) -> None:
        for operation, (required, optional) in OPERATION_FIELDS.items():
            if operation is self.operation:
                missing = [name for name in required if getattr(self, name) is None]
                if missing:
                    raise ValueError(f"{operation.value} needs {', '.join(required)}")
                if (self.geometry, self.attributes, self.layer) != (None, None, None):
                    raise ValueError(f"{operation.value} takes no geometry, attributes or layer")
            elif any(getattr(self, name) is not None for name in (*required, *optional)):
                raise ValueError(
                    f"{', '.join((*required, *optional))} are for {operation.value} only"
                )
        pair = {
            EntityOperation.TRIANGULATE: (self.ref_a, self.ref_b),
            EntityOperation.BASELINE_OFFSET: (self.base_a, self.base_b),
        }.get(self.operation)
        if pair is not None and pair[0] == pair[1]:
            raise ValueError("the two references must be different entities")


class EntitySource(BaseModel):
    """Provenance of an entity; kept unchanged by edits (history lives in the events)."""

    method: SourceMethod
    job_id: uuid.UUID | None = None
    parent_entity_id: uuid.UUID | None = None


class EntityOut(BaseModel):
    """An entity of spec section 8.2; ``created_by`` is null for system entities.

    ``discrepancy`` is set for dimensions only (null for other types): true when
    ``|declared_value_m - measured_value_m|`` exceeds ``dimension.discrepancy_tolerance_m``.
    It is computed on every read and never stored.
    """

    id: uuid.UUID
    revision_id: uuid.UUID
    revision_number: int
    type: EntityType
    layer: str
    geometry: Geometry
    image_region: dict[str, Any] | None
    attributes: dict[str, Any]
    evidence_id: uuid.UUID | None
    constraint: dict[str, Any] | None
    source: EntitySource
    confidence: float | None
    model_version: str | None
    review_status: ReviewStatus
    metric_origin: MetricOrigin
    created_by: uuid.UUID | None
    updated_at: datetime
    deleted_at: datetime | None
    discrepancy: bool | None


class EntityPatchOut(EntityOut):
    """The edited entity; ``triangulate`` also returns its candidate positions.

    ``candidates`` holds the 1 (tangent circles) or 2 intersection points, the first to the
    left of the direction from ``ref_a`` to ``ref_b``; ``chosen_candidate`` is the index of
    the one applied. Both are null for every other operation.
    """

    candidates: list[Coordinate] | None = None
    chosen_candidate: int | None = None


class EntityListOut(BaseModel):
    """Entities of a revision, ordered by id; soft-deleted ones are left out."""

    entities: list[EntityOut]
