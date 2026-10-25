"""Plan revisions: editable drafts and immutable frozen snapshots (spec section 8.3)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
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


class RevisionState(StrEnum):
    """Revision states; enforced by the ``ck_revision_state`` check constraint."""

    DRAFT = "draft"
    FROZEN = "frozen"


REVISION_STATE_CHECK = "state IN ('draft', 'frozen')"
FROZEN_SNAPSHOT_CHECK = (
    "(state = 'draft' AND snapshot IS NULL AND snapshot_sha256 IS NULL AND frozen_at IS NULL)"
    " OR (state = 'frozen' AND snapshot IS NOT NULL AND snapshot_sha256 IS NOT NULL"
    " AND frozen_at IS NOT NULL)"
)


class Revision(Base):
    """Revision ``number`` of a plan; frozen revisions carry the canonical snapshot.

    Freezing is the single ``UPDATE`` that sets ``state = 'frozen'`` together with
    ``snapshot``, ``snapshot_sha256`` (SHA-256 of the JCS bytes) and ``frozen_at``; the
    ``ck_revision_frozen_snapshot`` check requires all four together. Migration
    ``0005_plans_editor`` installs the trigger ``revision_frozen_immutable``, which rejects
    every ``UPDATE`` and ``DELETE`` of a row that is already frozen.
    """

    __tablename__ = "revision"
    __table_args__ = (
        CheckConstraint(REVISION_STATE_CHECK, name="state"),
        CheckConstraint(FROZEN_SNAPSHOT_CHECK, name="frozen_snapshot"),
        UniqueConstraint("plan_id", "number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plan.id"))
    number: Mapped[int] = mapped_column(Integer)
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("revision.id"))
    state: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    snapshot_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
