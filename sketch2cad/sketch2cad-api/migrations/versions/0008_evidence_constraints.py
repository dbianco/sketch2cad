"""Evidence register (H3): ``evidence`` table, ``entity.evidence_id`` FK, constraint kind.

``evidence`` numbers the evidence of a case file: ``number`` is unique per case file
(``uq_evidence_case_file_id_number``) and starts at 1. ``entity.evidence_id`` already
existed as a plain UUID column (0005); it now references ``evidence(id)``. Existing rows
hold NULL there, so the foreign key applies without data changes. ``entity.constraint``
also existed; a check now limits its ``kind`` to ``triangulation`` and
``baseline_offset`` (the full shape is validated by the API). Revisions without a
``constraint`` stay valid.

Revision ID: 0008_evidence_constraints
Revises: 0007_calibration_as_metric
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_evidence_constraints"
down_revision: str | Sequence[str] | None = "0007_calibration_as_metric"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literal copies so this revision does not change if the models change later.
EVIDENCE_NUMBER_CHECK = "number >= 1"
CONSTRAINT_KIND_CHECK = (
    "\"constraint\" IS NULL OR \"constraint\"->>'kind' IN ('triangulation', 'baseline_offset')"
)


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(EVIDENCE_NUMBER_CHECK, name=op.f("ck_evidence_number")),
        sa.ForeignKeyConstraint(
            ["case_file_id"], ["case_file.id"], name=op.f("fk_evidence_case_file_id_case_file")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], name=op.f("fk_evidence_created_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
        sa.UniqueConstraint("case_file_id", "number", name=op.f("uq_evidence_case_file_id_number")),
    )
    op.create_foreign_key(
        op.f("fk_entity_evidence_id_evidence"), "entity", "evidence", ["evidence_id"], ["id"]
    )
    op.create_index(op.f("ix_entity_evidence_id"), "entity", ["evidence_id"])
    op.create_check_constraint(
        op.f("ck_entity_constraint_kind"), "entity", sa.text(CONSTRAINT_KIND_CHECK)
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_entity_constraint_kind"), "entity", type_="check")
    op.drop_index(op.f("ix_entity_evidence_id"), table_name="entity")
    op.drop_constraint(op.f("fk_entity_evidence_id_evidence"), "entity", type_="foreignkey")
    op.drop_table("evidence")
