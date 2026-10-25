"""Derived raster of PDF sources: nullable ``source.raster_key``.

Single-page PDFs are rasterized at 300 dpi by the worker (task ``rasterize``); the PNG is
a write-once storage object and ``raster_key`` is its storage key. The column stays null
for image sources and for PDFs not rasterized yet.

Revision ID: 0004_source_raster
Revises: 0003_global_auth_chain
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_source_raster"
down_revision: str | Sequence[str] | None = "0003_global_auth_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source", sa.Column("raster_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("source", "raster_key")
