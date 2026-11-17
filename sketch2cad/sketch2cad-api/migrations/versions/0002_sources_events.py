"""Sources, hash-chained events and job metadata; events are immutable.

``event`` is append-only: the row-level trigger ``event_immutable`` runs
``reject_event_mutation()`` before every ``UPDATE`` or ``DELETE`` and raises, so the
statement fails and its transaction aborts (H1-7). Inserts are unaffected. The unique
index ``(case_file_id, id)`` supports walking one case file's chain in order.

Events of H0 (case creation and logins) were never recorded; this migration does not
backfill them.

Revision ID: 0002_sources_events
Revises: 0001_initial
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_sources_events"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_DEFAULT = sa.text("gen_random_uuid()")
EMPTY_JSONB = sa.text("'{}'::jsonb")
MEDIA_TYPE_CHECK = "media_type IN ('image/jpeg', 'image/png', 'image/tiff', 'application/pdf')"
JOB_STATUS_CHECK = "status IN ('queued', 'running', 'succeeded', 'failed')"

CREATE_REJECT_FUNCTION = """
CREATE FUNCTION reject_event_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'event is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$
"""
CREATE_IMMUTABLE_TRIGGER = """
CREATE TRIGGER event_immutable
BEFORE UPDATE OR DELETE ON event
FOR EACH ROW EXECUTE FUNCTION reject_event_mutation()
"""


def upgrade() -> None:
    op.create_table(
        "source",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=True),
        sa.Column("height_px", sa.Integer(), nullable=True),
        sa.Column("dpi", sa.Numeric(), nullable=True),
        sa.Column("exif", postgresql.JSONB(), server_default=EMPTY_JSONB, nullable=False),
        sa.Column("qc", postgresql.JSONB(), nullable=True),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(MEDIA_TYPE_CHECK, name=op.f("ck_source_media_type")),
        sa.ForeignKeyConstraint(
            ["case_file_id"], ["case_file.id"], name=op.f("fk_source_case_file_id_case_file")
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["user.id"], name=op.f("fk_source_uploaded_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source")),
    )
    op.create_index(op.f("ix_source_case_file_id"), "source", ["case_file_id"])
    op.create_index(op.f("ix_source_uploaded_by"), "source", ["uploaded_by"])

    op.create_table(
        "event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prev_hash", sa.CHAR(64), nullable=False),
        sa.Column("hash", sa.CHAR(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_file_id"], ["case_file.id"], name=op.f("fk_event_case_file_id_case_file")
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], name=op.f("fk_event_actor_id_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event")),
    )
    op.create_index(op.f("ix_event_case_file_id_id"), "event", ["case_file_id", "id"], unique=True)
    op.execute(CREATE_REJECT_FUNCTION)
    op.execute(CREATE_IMMUTABLE_TRIGGER)

    op.create_table(
        "job_meta",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(), server_default=EMPTY_JSONB, nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(JOB_STATUS_CHECK, name=op.f("ck_job_meta_status")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_job_meta_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_meta")),
    )
    op.create_index(op.f("ix_job_meta_source_id"), "job_meta", ["source_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_job_meta_source_id"), table_name="job_meta")
    op.drop_table("job_meta")
    op.execute("DROP TRIGGER event_immutable ON event")
    op.execute("DROP FUNCTION reject_event_mutation()")
    op.drop_index(op.f("ix_event_case_file_id_id"), table_name="event")
    op.drop_table("event")
    op.drop_index(op.f("ix_source_uploaded_by"), table_name="source")
    op.drop_index(op.f("ix_source_case_file_id"), table_name="source")
    op.drop_table("source")
