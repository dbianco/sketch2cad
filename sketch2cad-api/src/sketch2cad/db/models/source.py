"""Uploaded sources (sketches, scans, photos, PDFs) attached to a case file."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base


class MediaType(StrEnum):
    """Accepted media types; enforced by the ``ck_source_media_type`` check constraint."""

    JPEG = "image/jpeg"
    PNG = "image/png"
    TIFF = "image/tiff"
    PDF = "application/pdf"


MEDIA_TYPE_CHECK = "media_type IN ('image/jpeg', 'image/png', 'image/tiff', 'application/pdf')"


class Source(Base):
    """An original file stored by content (SHA-256) with its metadata and quality checks.

    ``qc`` stays null until the quality-control job runs; once set it holds
    ``{"status": "green"|"amber"|"red", "checks": [{name, status, value, threshold,
    message}]}``.
    """

    __tablename__ = "source"
    __table_args__ = (CheckConstraint(MEDIA_TYPE_CHECK, name="media_type"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    case_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_file.id"), index=True)
    original_filename: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    storage_key: Mapped[str] = mapped_column(Text)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    dpi: Mapped[Decimal | None] = mapped_column(Numeric)
    exif: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    qc: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
