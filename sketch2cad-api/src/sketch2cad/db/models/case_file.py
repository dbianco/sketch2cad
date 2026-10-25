"""Case files, their members and the per-year case code counter."""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base
from sketch2cad.db.models.user import enum_values


class IncidentType(StrEnum):
    """Kind of incident; stored as the PostgreSQL enum ``incident_type``."""

    TRAFFIC_COLLISION = "traffic_collision"
    CRIME_SCENE = "crime_scene"
    INSPECTION = "inspection"
    OTHER = "other"


class CaseFile(Base):
    """A case file (``EXP-<year>-<NNNN>``) grouping sources and plans."""

    __tablename__ = "case_file"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(Text, unique=True)
    incident_type: Mapped[IncidentType] = mapped_column(
        Enum(IncidentType, name="incident_type", values_callable=enum_values)
    )
    incident_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    declared_location: Mapped[str] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    # Reserved for the retention policies of spec section 8.1; no logic yet.
    retention_policy_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    legal_hold: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CaseMember(Base):
    """Membership of a user in a case file with explicit permissions."""

    __tablename__ = "case_member"

    case_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("case_file.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    permissions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))


class CaseCodeCounter(Base):
    """Last case code number issued per year; support object, not an application table.

    The generator increments a row atomically with
    ``INSERT ... ON CONFLICT (year) DO UPDATE SET last_number = last_number + 1
    RETURNING last_number``, which serializes concurrent creations on the row lock.
    """

    __tablename__ = "case_code_counter"

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_number: Mapped[int] = mapped_column(Integer, server_default=text("0"))
