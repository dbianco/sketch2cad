"""Seed the ``default-1`` catalog: ten layers and eight symbols (spec section 12).

The data comes from :func:`sketch2cad.catalog.default_catalog`, a frozen version. The
upgrade is idempotent: an existing ``default-1`` row is left untouched, and the seed is
only made active when no other catalog is active (at most one active catalog). The
downgrade deletes the ``default-1`` row only; other catalog versions stay.

Revision ID: 0006_seed_catalog
Revises: 0005_plans_editor
Create Date: 2026-10-25
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sketch2cad.catalog import catalog_json, default_catalog

revision: str = "0006_seed_catalog"
down_revision: str | Sequence[str] | None = "0005_plans_editor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_VERSION = "default-1"


def upgrade() -> None:
    data = catalog_json(default_catalog())
    assert data["version"] == SEED_VERSION  # noqa: S101 - guards against a swapped seed
    op.execute(
        sa.text(
            "INSERT INTO catalog (version, is_active, layers, symbols) "
            "SELECT :version, NOT EXISTS (SELECT 1 FROM catalog WHERE is_active), "
            "CAST(:layers AS jsonb), CAST(:symbols AS jsonb) "
            "ON CONFLICT (version) DO NOTHING"
        ).bindparams(
            version=SEED_VERSION,
            layers=json.dumps(data["layers"], ensure_ascii=False),
            symbols=json.dumps(data["symbols"], ensure_ascii=False),
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM catalog WHERE version = :version").bindparams(version=SEED_VERSION)
    )
