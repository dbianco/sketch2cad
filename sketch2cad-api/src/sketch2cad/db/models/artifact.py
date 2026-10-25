"""Generated artifacts of a plan: rectified image and exports (spec sections 8.1, 13)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base


class ArtifactKind(StrEnum):
    """Artifact kinds; enforced by the ``ck_artifact_kind`` check constraint."""

    DXF = "dxf"
    PDF_PLAN = "pdf_plan"
    PDF_AUDIT = "pdf_audit"
    MANIFEST = "manifest"
    ZIP = "zip"
    RECTIFIED_IMAGE = "rectified_image"


ARTIFACT_KIND_CHECK = (
    "kind IN ('dxf', 'pdf_plan', 'pdf_audit', 'manifest', 'zip', 'rectified_image')"
)


class Artifact(Base):
    """A write-once storage object derived from a plan, addressed by its SHA-256.

    ``revision_id`` is null for artifacts that belong to the plan rather than to a
    revision, such as the ``rectified_image`` produced when a calibration is accepted.
    """

    __tablename__ = "artifact"
    __table_args__ = (
        CheckConstraint(ARTIFACT_KIND_CHECK, name="kind"),
        Index("ix_artifact_plan_id_revision_id_kind", "plan_id", "revision_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plan.id"))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("revision.id"))
    kind: Mapped[str] = mapped_column(Text)
    is_draft: Mapped[bool] = mapped_column(Boolean)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    storage_key: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    generator_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
