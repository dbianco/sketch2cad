"""Plan endpoints: create a plan from a source, retrieve it, set its local frame and
acquire or release its edit lease.

REQ-03, US-04; the local frame is REQ-04, US-05; the edit lease is REQ-05, V-12.
"""

import logging
import uuid
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, FiniteFloat, StringConstraints
from sqlalchemy.orm import Session

from sketch2cad.api.deps import (
    get_current_user,
    require_case_permission,
    require_csrf,
    require_plan_permission,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.api.routers.entities import EDIT_DEPENDENCIES
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
from sketch2cad.geometry.frame import LocalFrame
from sketch2cad.plans.lease import (
    LeaseConflictError,
    LeaseHolder,
    acquire_lease,
    current_holder,
    release_lease,
)
from sketch2cad.plans.local_frame import LocalFrameConflictError, set_local_frame
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

LOCAL_FRAME_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``edit`` on the plan's case file (access.denied)."},
    404: {"description": "The plan does not exist."},
    409: {
        "description": "The plan has no accepted calibration, its revision is frozen, it "
        "is not in EDITING or IN_REVIEW (``code`` ``V-10`` when validated), or the user "
        "does not hold its edit lease (``code`` ``V-12`` with ``holder``, or "
        "``lease_required``).",
    },
    422: {"description": "Invalid body."},
}

_LEASE_DENIED = "The user lacks ``edit`` on the plan's case file (access.denied)."
LEASE_HELD = "Another user holds a valid lease: ``code`` ``V-12``, ``holder`` and the V-12 message."
ACQUIRE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": _LEASE_DENIED},
    404: {"description": "The plan does not exist."},
    409: {"description": LEASE_HELD},
}
RELEASE_RESPONSES = ACQUIRE_RESPONSES

RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PlanCreate(BaseModel):
    title: RequiredText
    primary_source_id: uuid.UUID
    source_mode: SourceMode = SourceMode.SCHEMATIC_SKETCH


class LocalFrameIn(BaseModel):
    """The local frame: origin on paper (mm, Y down), +X axis and North angles in degrees.

    Angles are counter-clockwise positive: ``x_axis_angle_deg`` from the world +X axis,
    ``north_angle_deg`` from the local +Y axis. They are stored reduced to ``[0, 360)``.
    """

    model_config = ConfigDict(extra="forbid")

    origin_paper_mm: tuple[FiniteFloat, FiniteFloat]
    x_axis_angle_deg: FiniteFloat
    north_angle_deg: FiniteFloat


class LocalFrameOut(BaseModel):
    """The stored ``calibration.local_frame``; angles in ``[0, 360)``."""

    origin_paper_mm: tuple[float, float]
    x_axis_angle_deg: float
    north_angle_deg: float


class CalibrationOut(BaseModel):
    """The active calibration; ``image_to_paper`` is the 3x3 matrix in mm per pixel."""

    id: uuid.UUID
    method: str
    image_to_paper: list[list[float]] | None
    control_points: list[dict[str, Any]]
    reprojection_error_mm: dict[str, Any] | None
    paper_scale: int | None
    local_frame: LocalFrameOut | None
    as_metric: bool
    accepted_by: uuid.UUID
    accepted_at: datetime


class LeaseOut(BaseModel):
    """The valid edit lease of a plan: who holds it and until when.

    ``display_name`` is the holder's display name, or their username when none is set.
    """

    user_id: uuid.UUID
    display_name: str
    expires_at: datetime


class LeaseReleaseOut(BaseModel):
    """Outcome of a release: ``released`` is false when the caller held no lease."""

    released: bool


class RevisionSummaryOut(BaseModel):
    id: uuid.UUID
    number: int
    state: str


class PlanOut(BaseModel):
    """A plan with its active calibration, edit lease and current revision.

    ``calibration`` is null until a calibration is accepted. ``lease`` is null while no
    user holds a valid (unexpired) edit lease.
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
        local_frame=None
        if calibration.local_frame is None
        else LocalFrameOut.model_validate(calibration.local_frame),
        as_metric=calibration.as_metric,
        accepted_by=calibration.accepted_by,
        accepted_at=calibration.accepted_at,
    )


def _revision_out(revision: Revision | None) -> RevisionSummaryOut | None:
    if revision is None:
        return None
    return RevisionSummaryOut(id=revision.id, number=revision.number, state=revision.state)


def lease_out(holder: LeaseHolder) -> LeaseOut:
    return LeaseOut(
        user_id=holder.user_id, display_name=holder.display_name, expires_at=holder.expires_at
    )


def _out(
    plan: Plan,
    calibration: Calibration | None,
    revision: Revision | None,
    lease: LeaseHolder | None = None,
) -> PlanOut:
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
        lease=None if lease is None else lease_out(lease),
        current_revision=_revision_out(revision),
    )


def plan_out(db: Session, plan: Plan) -> PlanOut:
    """The API form of ``plan`` with its active calibration, valid lease and revision."""
    return _out(
        plan, active_calibration(db, plan), current_revision(db, plan), current_holder(db, plan)
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
    return plan_out(db, plan)


@router.put(
    "/plans/{plan_id}/local-frame", dependencies=EDIT_DEPENDENCIES, responses=LOCAL_FRAME_RESPONSES
)
def put_local_frame(
    request: Request,
    body: LocalFrameIn,
    plan: Annotated[Plan, Depends(require_plan_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> PlanOut:
    """Set the plan's local frame on its active calibration. Requires ``edit``.

    A change of origin or +X axis moves every live entity of the draft revision by one
    rigid transform in one transaction. The first frame writes ``local_frame.set``; later
    changes write one ``local_frame.changed`` with the previous and new frame and the
    transform (null when only the North changes). An unchanged frame writes nothing.
    """
    frame = LocalFrame.create(body.origin_paper_mm, body.x_axis_angle_deg, body.north_angle_deg)
    try:
        set_local_frame(db, plan, user, frame, request.client.host if request.client else None)
    except LocalFrameConflictError as exc:
        db.rollback()
        logger.warning("Rejected local frame of plan %s: %s", plan.id, type(exc).__name__)
        extensions = {**exc.extensions, **({} if exc.code is None else {"code": exc.code})}
        raise ProblemError(
            HTTPStatus.CONFLICT, HTTPStatus.CONFLICT.phrase, str(exc), extensions=extensions
        ) from None
    db.commit()
    return plan_out(db, plan)


def _lease_conflict(db: Session, plan: Plan, exc: LeaseConflictError) -> ProblemError:
    db.rollback()
    logger.warning("Rejected lease request on plan %s: %s", plan.id, exc.code)
    return ProblemError(
        HTTPStatus.CONFLICT,
        HTTPStatus.CONFLICT.phrase,
        str(exc),
        extensions={"code": exc.code, **exc.extensions},
    )


@router.post(
    "/plans/{plan_id}/lease", dependencies=[Depends(require_csrf)], responses=ACQUIRE_RESPONSES
)
def post_lease(
    request: Request,
    plan: Annotated[Plan, Depends(require_plan_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> LeaseOut:
    """Acquire or renew the plan's edit lease for 10 minutes. Requires ``edit``.

    A renewal by the holder writes no event; a first acquisition, or one after the lease
    expired, writes ``lease.acquired`` (``before`` is the previous lease). 409 with
    ``holder`` and the V-12 message while another user holds a valid lease.
    """
    try:
        holder = acquire_lease(db, plan, user, request.client.host if request.client else None)
    except LeaseConflictError as exc:
        raise _lease_conflict(db, plan, exc) from None
    db.commit()
    return lease_out(holder)


@router.delete(
    "/plans/{plan_id}/lease", dependencies=[Depends(require_csrf)], responses=RELEASE_RESPONSES
)
def delete_lease(
    request: Request,
    plan: Annotated[Plan, Depends(require_plan_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> LeaseReleaseOut:
    """Release the caller's edit lease with ``lease.released``. Requires ``edit``.

    Only the holder releases. ``released`` is false, and nothing is written, when the
    caller holds no lease; 409 with ``holder`` while another user holds a valid one.
    """
    try:
        released = release_lease(db, plan, user, request.client.host if request.client else None)
    except LeaseConflictError as exc:
        raise _lease_conflict(db, plan, exc) from None
    db.commit()
    return LeaseReleaseOut(released=released)
