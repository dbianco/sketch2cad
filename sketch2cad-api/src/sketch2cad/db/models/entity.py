"""Drawing entities of a revision (spec section 8.2)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base


class EntityType(StrEnum):
    """Entity types; enforced by the ``ck_entity_type`` check constraint."""

    LINE = "line"
    POLYLINE = "polyline"
    POINT = "point"
    TEXT = "text"
    DIMENSION = "dimension"
    SYMBOL = "symbol"


class ReviewStatus(StrEnum):
    """Review state of an entity; enforced by ``ck_entity_review_status``."""

    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class MetricOrigin(StrEnum):
    """Where the metric value of an entity comes from; enforced by ``ck_entity_metric_origin``."""

    DECLARED_DIMENSION = "declared_dimension"
    TRIANGULATION = "triangulation"
    BASELINE_OFFSET = "baseline_offset"
    SCALED_DRAWING = "scaled_drawing"
    DERIVED = "derived"
    TRACE_ONLY = "trace_only"
    NONE = "none"


ENTITY_TYPE_CHECK = "type IN ('line', 'polyline', 'point', 'text', 'dimension', 'symbol')"
REVIEW_STATUS_CHECK = "review_status IN ('suggested', 'accepted', 'corrected', 'rejected')"
METRIC_ORIGIN_CHECK = (
    "metric_origin IN ('declared_dimension', 'triangulation', 'baseline_offset', "
    "'scaled_drawing', 'derived', 'trace_only', 'none')"
)
CONFIDENCE_CHECK = "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)"
CONSTRAINT_KIND_CHECK = (
    "\"constraint\" IS NULL OR \"constraint\"->>'kind' IN ('triangulation', 'baseline_offset')"
)


class Entity(Base):
    """One entity with its own geometry in metres in the local frame (Y up).

    ``deleted_at`` marks a soft delete while the revision is a draft; deleted entities
    are left out of the frozen snapshot but stay in the events. ``evidence_id`` points to
    the case-file evidence the entity places. ``constraint`` holds the metric constraint
    of an entity fixed by triangulation or baseline plus offset (shapes in
    ``sketch2cad.entities.constraints``); the database only checks its ``kind``.
    ``created_by`` is null for entities created by the system.
    """

    __tablename__ = "entity"
    __table_args__ = (
        CheckConstraint(ENTITY_TYPE_CHECK, name="type"),
        CheckConstraint(REVIEW_STATUS_CHECK, name="review_status"),
        CheckConstraint(METRIC_ORIGIN_CHECK, name="metric_origin"),
        CheckConstraint(CONFIDENCE_CHECK, name="confidence"),
        CheckConstraint(CONSTRAINT_KIND_CHECK, name="constraint_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("revision.id"), index=True)
    type: Mapped[str] = mapped_column(Text)
    layer: Mapped[str] = mapped_column(Text)
    geometry: Mapped[dict[str, Any]] = mapped_column(JSONB)
    image_region: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id"), index=True)
    constraint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Double)
    model_version: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(Text)
    metric_origin: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
