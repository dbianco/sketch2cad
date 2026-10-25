"""Request and response models of drawing entities (spec section 8.2).

Geometry is a union discriminated by ``kind``, which must equal the entity ``type``.
Coordinates are metres in the local frame, Y up. Numbers must be JSON numbers and finite;
strings are never coerced. ``dimension.measured_value_m`` is always recomputed from
``p1_m`` and ``p2_m``: the server is the authority on measurements.
"""

import math
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
    """``declared_value_m`` is null for a plain measurement; ``measured_value_m`` is computed."""

    kind: Literal["dimension"]
    p1_m: Coordinate
    p2_m: Coordinate
    offset_m: Number = 0.0
    declared_value_m: PositiveNumber | None = None
    measured_value_m: Number | None = None

    @model_validator(mode="after")
    def _measure(self) -> "DimensionGeometry":
        (x1, y1), (x2, y2) = self.p1_m, self.p2_m
        self.measured_value_m = math.hypot(x2 - x1, y2 - y1)
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
    """A new entity drawn by hand; provenance, review and metric fields are set by the server."""

    type: EntityType
    layer: LayerName
    geometry: Geometry
    attributes: Attributes = Field(default_factory=dict[str, AttributeValue])

    @model_validator(mode="after")
    def _kind_matches_type(self) -> "EntityIn":
        if self.geometry.kind != self.type.value:
            raise ValueError("geometry.kind must equal type")
        return self


class EntitiesCreate(_Strict):
    entities: Annotated[list[EntityIn], Field(min_length=1, max_length=MAX_ENTITIES_PER_REQUEST)]


class EntityPatch(_Strict):
    """An edit. ``geometry`` replaces the whole geometry and keeps the entity's ``kind``.

    ``move_vertex`` and the ``snap_*`` operations need ``geometry``; ``reclassify`` needs
    ``layer``; ``relabel`` needs ``geometry`` or ``attributes``.
    """

    operation: EntityOperation
    geometry: Geometry | None = None
    attributes: Attributes | None = None
    layer: LayerName | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None

    @model_validator(mode="after")
    def _operation_has_its_fields(self) -> "EntityPatch":
        if self.operation in GEOMETRY_OPERATIONS and self.geometry is None:
            raise ValueError(f"{self.operation.value} needs geometry")
        if self.operation is EntityOperation.RECLASSIFY and self.layer is None:
            raise ValueError("reclassify needs layer")
        if self.operation is EntityOperation.RELABEL and (
            self.geometry is None and self.attributes is None
        ):
            raise ValueError("relabel needs geometry or attributes")
        return self


class EntitySource(BaseModel):
    """Provenance of an entity; kept unchanged by edits (history lives in the events)."""

    method: SourceMethod
    job_id: uuid.UUID | None = None
    parent_entity_id: uuid.UUID | None = None


class EntityOut(BaseModel):
    """An entity of spec section 8.2; ``created_by`` is null for system entities."""

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


class EntityListOut(BaseModel):
    """Entities of a revision, ordered by id; soft-deleted ones are left out."""

    entities: list[EntityOut]
