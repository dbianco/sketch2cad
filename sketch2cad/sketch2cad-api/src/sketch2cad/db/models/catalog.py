"""Versioned catalog of layers and symbols (spec section 12)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base

ONE_ACTIVE_CATALOG_INDEX = "ux_catalog_active"


class Catalog(Base):
    """One catalog version (``default-1``) with its layers and symbols as JSON lists.

    ``layers`` follows spec section 12.1 and ``symbols`` the symbol format of section
    12.2. At most one catalog is active (partial unique index ``ux_catalog_active``).
    The ``default-1`` seed is a separate data migration.
    """

    __tablename__ = "catalog"
    __table_args__ = (
        Index(
            ONE_ACTIVE_CATALOG_INDEX, "is_active", unique=True, postgresql_where=text("is_active")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    version: Mapped[str] = mapped_column(Text, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    layers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    symbols: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
