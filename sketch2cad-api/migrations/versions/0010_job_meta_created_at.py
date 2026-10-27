"""Creation time of a job: ``job_meta.created_at``.

``GET /plans/{id}/jobs`` lists the jobs of a plan newest first, so the editor can follow
the ``rectify`` and ``suggest_lines`` jobs queued when a calibration is accepted. Existing
rows get the migration time. An index on ``(plan_id, created_at)`` serves that list.

Revision ID: 0010_job_meta_created_at
Revises: 0009_config_override
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_job_meta_created_at"
down_revision: str | Sequence[str] | None = "0009_config_override"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_meta",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_job_meta_plan_id_created_at", "job_meta", ["plan_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_job_meta_plan_id_created_at", table_name="job_meta")
    op.drop_column("job_meta", "created_at")
