"""Append-only, hash-chained audit events (spec section 14.1)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, BigInteger, DateTime, ForeignKey, Index, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base


class Event(Base):
    """One domain action, chained per case file by ``hash = SHA-256(prev_hash || JCS(event))``.

    Events without a case file (``case_file_id`` null, migration ``0003_global_auth_chain``)
    form the single global auth chain described in ``docs/audit.md``.

    Migration ``0002_sources_events`` installs a trigger that rejects every ``UPDATE`` and
    ``DELETE`` on this table. ``occurred_at`` has no server default on purpose: it is part
    of the hashed payload, so the writer must set it explicitly. ``plan_id``,
    ``revision_id`` and ``entity_id`` have no foreign key on purpose (see migration
    ``0005_plans_editor``): the log must outlive the rows it mentions, and ``entity_id``
    is the generic subject of an event (a source for the ``source.*`` kinds).
    """

    __tablename__ = "event"
    __table_args__ = (
        Index("ix_event_case_file_id_id", "case_file_id", "id", unique=True),
        Index("ix_event_global_chain_id", "id", postgresql_where=text("case_file_id IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_file.id"))
    plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    revision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))
    actor_role: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(CHAR(64))
    hash: Mapped[str] = mapped_column(CHAR(64))
