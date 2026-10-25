"""Image-to-paper calibrations of a plan and its local frame (spec sections 8.1, 11)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sketch2cad.db.base import Base


class CalibrationMethod(StrEnum):
    """Calibration methods; enforced by the ``ck_calibration_method`` check constraint."""

    SCAN_DPI = "scan_dpi"
    SCAN_SEGMENT = "scan_segment"
    PHOTO_HOMOGRAPHY_4PT = "photo_homography_4pt"
    NONE = "none"


CALIBRATION_METHOD_CHECK = "method IN ('scan_dpi', 'scan_segment', 'photo_homography_4pt', 'none')"
PAPER_SCALE_CHECK = "paper_scale IS NULL OR paper_scale > 0"
ONE_ACTIVE_CALIBRATION_INDEX = "ux_calibration_plan_id_active"


class Calibration(Base):
    """An accepted calibration; at most one row per plan has ``is_active`` set.

    ``image_to_paper`` is the 3x3 matrix (mm per px) as nested JSON lists, null for
    ``none``. ``reprojection_error_mm`` holds ``{"max", "rms"}``; ``local_frame`` holds
    ``{"origin_paper_mm", "x_axis_angle_deg", "north_angle_deg"}`` once it is set.
    A new calibration deactivates the previous one in the same transaction; the partial
    unique index ``ux_calibration_plan_id_active`` rejects a second active row.
    """

    __tablename__ = "calibration"
    __table_args__ = (
        CheckConstraint(CALIBRATION_METHOD_CHECK, name="method"),
        CheckConstraint(PAPER_SCALE_CHECK, name="paper_scale"),
        Index(
            ONE_ACTIVE_CALIBRATION_INDEX,
            "plan_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plan.id"), index=True)
    method: Mapped[str] = mapped_column(Text)
    image_to_paper: Mapped[list[list[float]] | None] = mapped_column(JSONB)
    control_points: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    reprojection_error_mm: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    paper_scale: Mapped[int | None] = mapped_column(Integer)
    local_frame: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    accepted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
