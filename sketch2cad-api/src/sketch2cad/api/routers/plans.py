"""Plan endpoints: create a plan from a source and retrieve it (REQ-03, US-04)."""

import logging
import uuid
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from sketch2cad.api.deps import (
    get_current_user,
    require_case_permission,
    require_csrf,
    require_plan_permission,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.db.models import (
    Calibration,
    CaseFile,
    MetricStatus,
    Plan,
    PlanStatus,
    Revision,
    SourceMode,
    User,
)
from sketch2cad.db.session import get_session
from sketch2cad.plans.service import (
    ForeignSourceError,
    NewPlan,
    active_calibration,
    create_plan,
    current_revision,
)

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["plans"])

CREATE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``edit`` on the case file (access.denied)."},
    404: {"description": "The case file does not exist."},
    422: {"description": "Invalid body, or the primary source is not a source of the case."},
}

GET_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``read`` on the plan's case file (access.denied)."},
    404: {"description": "The plan does not exist."},
}

RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PlanCreate(BaseModel):
    title: RequiredText
    primary_source_id: uuid.UUID
    source_mode: SourceMode = SourceMode.SCHEMATIC_SKETCH


class CalibrationOut(BaseModel):
    """The active calibration; ``image_to_paper`` is the 3x3 matrix in mm per pixel."""

    id: uuid.UUID
    method: str
    image_to_paper: list[list[float]] | None
    control_points: list[dict[str, Any]]
    reprojection_error_mm: dict[str, Any] | None
    paper_scale: int | None
    local_frame: dict[str, Any] | None
    accepted_by: uuid.UUID
    accepted_at: datetime


class LeaseOut(BaseModel):
    """The edit lease of a plan: who holds it and until when."""

    user_id: uuid.UUID
    expires_at: datetime


class RevisionSummaryOut(BaseModel):
    id: uuid.UUID
    number: int
    state: str


class PlanOut(BaseModel):
    """A plan with its active calibration, edit lease and current revision.

    ``calibration`` is null until a calibration is accepted. ``lease`` is null while no
    user holds the edit lease.
    """

    id: uuid.UUID
    case_file_id: uuid.UUID
    title: str
    primary_source_id: uuid.UUID
    source_mode: SourceMode
    status: PlanStatus
    metric_status: MetricStatus
    current_revision_id: uuid.UUID | None
    validated_revision_id: uuid.UUID | None
    created_at: datetime
    calibration: CalibrationOut | None
    lease: LeaseOut | None
    current_revision: RevisionSummaryOut | None


def _calibration_out(calibration: Calibration | None) -> CalibrationOut | None:
    if calibration is None:
        return None
    return CalibrationOut(
        id=calibration.id,
        method=calibration.method,
        image_to_paper=calibration.image_to_paper,
        control_points=calibration.control_points,
        reprojection_error_mm=calibration.reprojection_error_mm,
        paper_scale=calibration.paper_scale,
        local_frame=calibration.local_frame,
        accepted_by=calibration.accepted_by,
        accepted_at=calibration.accepted_at,
    )


def _revision_out(revision: Revision | None) -> RevisionSummaryOut | None:
    if revision is None:
        return None
    return RevisionSummaryOut(id=revision.id, number=revision.number, state=revision.state)


def _out(plan: Plan, calibration: Calibration | None, revision: Revision | None) -> PlanOut:
    return PlanOut(
        id=plan.id,
        case_file_id=plan.case_file_id,
        title=plan.title,
        primary_source_id=plan.primary_source_id,
        source_mode=plan.source_mode,
        status=plan.status,
        metric_status=plan.metric_status,
        current_revision_id=plan.current_revision_id,
        validated_revision_id=plan.validated_revision_id,
        created_at=plan.created_at,
        calibration=_calibration_out(calibration),
        # The edit lease (acquire, renew, expiry) arrives with the lease endpoints.
        lease=None,
        current_revision=_revision_out(revision),
    )


@router.post(
    "/case-files/{case_id}/plans",
    status_code=201,
    dependencies=[Depends(require_csrf)],
    responses=CREATE_RESPONSES,
)
def create_case_plan(
    request: Request,
    body: PlanCreate,
    case_file: Annotated[CaseFile, Depends(require_case_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> PlanOut:
    """Create a plan from a source of the case file, in ``CALIBRATION_PENDING``.

    ``source_mode`` defaults to ``schematic_sketch``. Creates revision 1 in ``draft`` and a
    ``plan.created`` event; enqueues no job. Requires ``edit``.
    """
    data = NewPlan(
        title=body.title, primary_source_id=body.primary_source_id, source_mode=body.source_mode
    )
    try:
        plan, revision = create_plan(
            db, case_file, user, data, request.client.host if request.client else None
        )
    except ForeignSourceError:
        db.rollback()
        logger.warning("Rejected plan in case file %s: foreign primary source", case_file.id)
        raise ProblemError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            HTTPStatus.UNPROCESSABLE_ENTITY.phrase,
            "The primary source is not a source of this case file.",
        ) from None
    db.commit()
    return _out(plan, None, revision)


@router.get("/plans/{plan_id}", responses=GET_RESPONSES)
def get_plan(
    plan: Annotated[Plan, Depends(require_plan_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
) -> PlanOut:
    """A plan with its status, active calibration, lease and current revision.

    Requires ``read`` on the plan's case file; others get 403 and an ``access.denied``
    event.
    """
    return _out(plan, active_calibration(db, plan), current_revision(db, plan))
