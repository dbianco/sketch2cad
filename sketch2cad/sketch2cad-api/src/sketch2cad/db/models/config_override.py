"""Overrides of configuration parameters, global or per incident type (REQ-19, spec 11.4, 19).

A row overrides the parameter ``key`` of :data:`sketch2cad.config_params.PARAMETERS` either
for every case (``scope = 'global'``, ``incident_type`` NULL) or for the case files of one
incident type (``scope = 'incident_type'``). Two partial unique indexes allow one override
per key and scope (and incident type). ``value`` is the JSON value, validated against the
parameter registry by the API before it is written.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base
from sketch2cad.db.models.case_file import IncidentType
from sketch2cad.db.models.user import enum_values


class ConfigScope(StrEnum):
    """Who an override applies to."""

    GLOBAL = "global"
    INCIDENT_TYPE = "incident_type"


SCOPE_CHECK = "scope IN ('global', 'incident_type')"
SCOPE_INCIDENT_TYPE_CHECK = "(scope = 'global') = (incident_type IS NULL)"
GLOBAL_UNIQUE_INDEX = "ux_config_override_global"
INCIDENT_TYPE_UNIQUE_INDEX = "ux_config_override_incident_type"


class ConfigOverride(Base):
    """One override of a configuration parameter."""

    __tablename__ = "config_override"
    __table_args__ = (
        CheckConstraint(SCOPE_CHECK, name="scope"),
        CheckConstraint(SCOPE_INCIDENT_TYPE_CHECK, name="scope_incident_type"),
        Index(GLOBAL_UNIQUE_INDEX, "key", unique=True, postgresql_where=text("scope = 'global'")),
        Index(
            INCIDENT_TYPE_UNIQUE_INDEX,
            "key",
            "incident_type",
            unique=True,
            postgresql_where=text("scope = 'incident_type'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    key: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    incident_type: Mapped[IncidentType | None] = mapped_column(
        Enum(IncidentType, name="incident_type", values_callable=enum_values)
    )
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
