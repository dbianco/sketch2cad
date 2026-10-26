"""Evidence register of a case file (spec sections 2.1 and 8.1)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base

EVIDENCE_NUMBER_CHECK = "number >= 1"


class Evidence(Base):
    """One piece of evidence, numbered uniquely within its case file.

    Evidence lives at case-file level: evidence no. 7 is the same in the general plan and
    in the detail plan, each of which places it with an entity whose ``evidence_id``
    points here. ``number`` is assigned by the case-file sequence (starting at 1); the
    ``uq_evidence_case_file_id_number`` constraint is the last line of defence against
    duplicates.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("case_file_id", "number"),
        CheckConstraint(EVIDENCE_NUMBER_CHECK, name="number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    case_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_file.id"))
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
