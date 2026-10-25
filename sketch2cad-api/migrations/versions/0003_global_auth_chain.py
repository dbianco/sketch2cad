"""Global auth chain: ``event.case_file_id`` becomes nullable (docs/audit.md).

Events without a case file (``auth.login``, ``auth.failed``) form one global chain whose
genesis hash is ``SHA-256(b"auth")``. The unique index ``(case_file_id, id)`` still covers
the per-case chains; the partial index ``ix_event_global_chain_id`` serves the newest-hash
lookup of the global chain (``WHERE case_file_id IS NULL ORDER BY id DESC LIMIT 1``).

The downgrade refuses to run while global events exist: events are append-only, so they
cannot be deleted to restore ``NOT NULL``.

Revision ID: 0003_global_auth_chain
Revises: 0002_sources_events
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_global_auth_chain"
down_revision: str | Sequence[str] | None = "0002_sources_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GLOBAL_CHAIN_INDEX = "ix_event_global_chain_id"
GLOBAL_CHAIN_FILTER = sa.text("case_file_id IS NULL")


def upgrade() -> None:
    op.alter_column("event", "case_file_id", existing_type=sa.Uuid(), nullable=True)
    op.create_index(GLOBAL_CHAIN_INDEX, "event", ["id"], postgresql_where=GLOBAL_CHAIN_FILTER)


def downgrade() -> None:
    global_events = op.get_bind().execute(
        sa.text("SELECT count(*) FROM event WHERE case_file_id IS NULL")
    )
    if global_events.scalar_one():
        raise RuntimeError("cannot downgrade: the global auth chain has append-only events")
    op.drop_index(GLOBAL_CHAIN_INDEX, table_name="event")
    op.alter_column("event", "case_file_id", existing_type=sa.Uuid(), nullable=False)
