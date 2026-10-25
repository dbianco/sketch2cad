"""Application-side metadata of background jobs run by Procrastinate."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base


class JobStatus(StrEnum):
    """Lifecycle of a job; enforced by the ``ck_job_meta_status`` check constraint."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


JOB_STATUS_CHECK = "status IN ('queued', 'running', 'succeeded', 'failed')"


class JobMeta(Base):
    """Parameters, status and outcome of one job (``qc``, suggestions, OCR, exports).

    ``error`` holds ``{"cause": ..., "action": ...}`` when the job failed (REQ-15).
    ``plan_id`` has no foreign key because the ``plan`` table arrives in H2.
    """

    __tablename__ = "job_meta"
    __table_args__ = (CheckConstraint(JOB_STATUS_CHECK, name="status"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source.id"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    result_ref: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
