"""Whether an accepted calibration is metric: ``calibration.as_metric``.

``POST /plans/{id}/calibration/accept`` takes ``as_metric``; the flag is kept on the row so
the metric status of the plan can later be recomputed from it (spec section 11.3: entities
are promoted to ``scaled_drawing`` only under a calibration accepted as metric). Existing
rows default to ``false``.

Revision ID: 0007_calibration_as_metric
Revises: 0006_seed_catalog
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_calibration_as_metric"
down_revision: str | Sequence[str] | None = "0006_seed_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calibration",
        sa.Column("as_metric", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("calibration", "as_metric")
