"""Plans: one drawing of a case file built from a primary source (spec sections 8.1, 9.2)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base
from sketch2cad.db.models.user import enum_values


class PlanStatus(StrEnum):
    """The nine plan states of spec section 9.2; stored as the PostgreSQL enum ``plan_status``."""

    CREATED = "CREATED"
    CALIBRATION_PENDING = "CALIBRATION_PENDING"
    EDITING = "EDITING"
    REVIEW_PENDING = "REVIEW_PENDING"
    IN_REVIEW = "IN_REVIEW"
    VALIDATED = "VALIDATED"
    EXPORTED = "EXPORTED"
    REJECTED = "REJECTED"
    REOPENED = "REOPENED"


class SourceMode(StrEnum):
    """How the source was drawn; stored as the PostgreSQL enum ``source_mode``."""

    SCALED_DRAWING = "scaled_drawing"
    SCHEMATIC_SKETCH = "schematic_sketch"


class MetricStatus(StrEnum):
    """Metric quality of a plan; stored as the PostgreSQL enum ``metric_status``."""

    METRIC = "metric"
    APPROXIMATE = "approximate"
    NON_METRIC = "non_metric"


class Plan(Base):
    """A plan of a case file, its workflow state and its current and validated revisions.

    ``edit_lease`` holds ``{"user_id", "expires_at"}`` while a user holds the edit lease.
    ``current_revision_id`` is null only between the insert of the plan and the insert of
    its first revision. ``metric_status`` starts as ``non_metric``: a plan without an
    accepted calibration is not metric.
    """

    __tablename__ = "plan"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    case_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_file.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    primary_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source.id"), index=True)
    source_mode: Mapped[SourceMode] = mapped_column(
        Enum(SourceMode, name="source_mode", values_callable=enum_values),
        server_default=text("'schematic_sketch'"),
    )
    status: Mapped[PlanStatus] = mapped_column(
        Enum(PlanStatus, name="plan_status", values_callable=enum_values),
        server_default=text("'CREATED'"),
    )
    metric_status: Mapped[MetricStatus] = mapped_column(
        Enum(MetricStatus, name="metric_status", values_callable=enum_values),
        server_default=text("'non_metric'"),
    )
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("revision.id", use_alter=True)
    )
    validated_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("revision.id", use_alter=True)
    )
    edit_lease: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
