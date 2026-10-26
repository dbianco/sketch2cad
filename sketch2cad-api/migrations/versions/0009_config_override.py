"""Configuration overrides (H5, REQ-19): ``config_override`` table.

One row overrides a configuration parameter globally (``scope = 'global'``, no incident
type) or for one incident type (``scope = 'incident_type'``, reusing the ``incident_type``
enum of ``case_file``). Partial unique indexes keep one override per key and scope (and
incident type). Parameters without a row keep their environment/code default.

Revision ID: 0009_config_override
Revises: 0008_evidence_constraints
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_config_override"
down_revision: str | Sequence[str] | None = "0008_evidence_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literal copies so this revision does not change if the models change later.
SCOPE_CHECK = "scope IN ('global', 'incident_type')"
SCOPE_INCIDENT_TYPE_CHECK = "(scope = 'global') = (incident_type IS NULL)"


def upgrade() -> None:
    incident_type = postgresql.ENUM(name="incident_type", create_type=False)
    op.create_table(
        "config_override",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("incident_type", incident_type, nullable=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(SCOPE_CHECK, name=op.f("ck_config_override_scope")),
        sa.CheckConstraint(
            SCOPE_INCIDENT_TYPE_CHECK, name=op.f("ck_config_override_scope_incident_type")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["user.id"], name=op.f("fk_config_override_updated_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_override")),
    )
    op.create_index(
        "ux_config_override_global",
        "config_override",
        ["key"],
        unique=True,
        postgresql_where=sa.text("scope = 'global'"),
    )
    op.create_index(
        "ux_config_override_incident_type",
        "config_override",
        ["key", "incident_type"],
        unique=True,
        postgresql_where=sa.text("scope = 'incident_type'"),
    )


def downgrade() -> None:
    op.drop_index("ux_config_override_incident_type", table_name="config_override")
    op.drop_index("ux_config_override_global", table_name="config_override")
    op.drop_table("config_override")
