"""Calibration endpoints: propose and accept an image-to-paper calibration (REQ-03, US-03).

``PUT /plans/{id}/calibration`` computes a proposal and persists nothing.
``POST /plans/{id}/calibration/accept`` takes the same inputs plus ``as_metric``,
recomputes the proposal on the server and stores it; there is no stored proposal to
reference, so the client resends what it last proposed.
"""

import logging
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel, Field, FiniteFloat, PositiveInt
from sqlalchemy.orm import Session

from sketch2cad.api.deps import (
    get_current_user,
    require_csrf,
    require_plan_permission,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.api.routers.plans import PlanOut, plan_out
from sketch2cad.calibration import (
    CalibrationConflictError,
    CalibrationInput,
    CalibrationInputError,
    CalibrationTolerances,
    MetricRejectedError,
    PointInput,
    accept,
    propose,
)
from sketch2cad.db.models import CalibrationMethod, Plan, User
from sketch2cad.db.session import get_session

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["calibration"])

PositiveMm = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Coordinate = tuple[FiniteFloat, FiniteFloat]

_COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``edit`` on the plan's case file (access.denied)."},
    404: {"description": "The plan does not exist."},
    409: {
        "description": "The plan is past editing, or it has entities (``code`` ``V-08``).",
    },
}
ACCEPT_CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "The plan is past editing, it has entities (``code`` ``V-08``), or "
        "the user does not hold its edit lease (``code`` ``V-12`` with ``holder``, or "
        "``lease_required``).",
    },
}
PROPOSE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_COMMON_RESPONSES,
    422: {"description": "Invalid body, or inputs that define no calibration for the method."},
}
ACCEPT_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_COMMON_RESPONSES,
    **ACCEPT_CONFLICT,
    422: {
        "description": "Invalid inputs, or ``as_metric`` not allowed: method ``none``, a "
        "``scaled_drawing`` without ``paper_scale``, or an error above "
        "``calibration.max_reprojection_error_mm`` (``code`` ``V-05``).",
    },
}


class PointIn(BaseModel):
    """A marked point: image pixel and, when known, its paper position in mm (Y down)."""

    image_px: Coordinate
    paper_mm: Coordinate | None = None


class DimensionsIn(BaseModel):
    """Paper size of the rectangle whose corners are the 4 control points."""

    width: PositiveMm
    height: PositiveMm


class CalibrationIn(BaseModel):
    """Calibration inputs.

    - ``scan_dpi``: nothing; the scale comes from the source DPI.
    - ``scan_segment``: 2 ``control_points`` and ``segment_mm``, their distance on paper.
    - ``photo_homography_4pt``: 4 ``control_points`` in order around the quadrilateral;
      each needs ``paper_mm`` unless ``known_dimensions_mm`` is given, which defaults them
      to the corners TL, TR, BR, BL.
    - ``none``: nothing.

    ``check_points`` (each with ``paper_mm``) do not take part in the fit; when present,
    ``reprojection_error_mm`` is measured on them. ``paper_scale`` (``100`` for 1:100)
    applies only to ``scaled_drawing`` plans.
    """

    method: CalibrationMethod
    control_points: list[PointIn] = Field(default_factory=list[PointIn], max_length=4)
    check_points: list[PointIn] = Field(default_factory=list[PointIn])
    known_dimensions_mm: DimensionsIn | None = None
    segment_mm: PositiveMm | None = None
    paper_scale: PositiveInt | None = None


class CalibrationAcceptIn(CalibrationIn):
    """Calibration inputs plus whether to accept the calibration as metric."""

    as_metric: bool


class ReprojectionErrorOut(BaseModel):
    max: float
    rms: float


class CalibrationPointOut(BaseModel):
    """A stored point: ``role`` ``control`` or ``check``; ``error_mm`` is its residual."""

    role: str
    image_px: list[float]
    paper_mm: list[float] | None
    error_mm: float | None


class ScaleOut(BaseModel):
    """Scan scale; ``within_tolerance`` compares the segment with the DPI when both exist."""

    mm_per_px: float
    dpi: float | None
    dpi_discrepancy_pct: float | None
    within_tolerance: bool | None


class CalibrationProposalOut(BaseModel):
    """A computed calibration that is not stored.

    ``image_to_paper`` is the 3x3 matrix (px to paper mm), null for ``none``.
    ``reprojection_error_basis`` says whether the error is measured on ``check_points`` or
    ``control_points``; both are null when there is no error to report. ``meets_tolerance``
    tells whether ``as_metric`` would be accepted on the error.
    """

    method: CalibrationMethod
    image_to_paper: list[list[float]] | None
    control_points: list[CalibrationPointOut]
    reprojection_error_mm: ReprojectionErrorOut | None
    reprojection_error_basis: str | None
    meets_tolerance: bool
    max_reprojection_error_mm: float
    paper_scale: int | None
    scale: ScaleOut | None


def _point(point: PointIn) -> PointInput:
    return PointInput(image_px=point.image_px, paper_mm=point.paper_mm)


def _input(body: CalibrationIn) -> CalibrationInput:
    dimensions = body.known_dimensions_mm
    return CalibrationInput(
        method=body.method,
        control_points=tuple(_point(p) for p in body.control_points),
        check_points=tuple(_point(p) for p in body.check_points),
        known_dimensions_mm=None if dimensions is None else (dimensions.width, dimensions.height),
        segment_mm=body.segment_mm,
        paper_scale=body.paper_scale,
    )


def _problem(status: HTTPStatus, exc: Exception, code: str | None = None, **extra: Any):
    extensions: dict[str, Any] = dict(extra)
    if code is not None:
        extensions["code"] = code
    return ProblemError(status, status.phrase, str(exc), extensions=extensions)


def _rejected(db: Session, plan: Plan, exc: Exception) -> ProblemError:
    db.rollback()
    logger.warning("Rejected calibration of plan %s: %s", plan.id, type(exc).__name__)
    if isinstance(exc, CalibrationConflictError):
        return _problem(HTTPStatus.CONFLICT, exc, exc.code, **exc.extensions)
    if isinstance(exc, MetricRejectedError):
        extra = {**exc.extensions, "cause": exc.cause, "action": exc.action}
        return _problem(HTTPStatus.UNPROCESSABLE_ENTITY, exc, exc.code, **extra)
    return _problem(HTTPStatus.UNPROCESSABLE_ENTITY, exc)


@router.put(
    "/plans/{plan_id}/calibration",
    dependencies=[Depends(require_csrf)],
    responses=PROPOSE_RESPONSES,
)
def propose_calibration(
    body: CalibrationIn,
    plan: Annotated[Plan, Depends(require_plan_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
) -> CalibrationProposalOut:
    """Compute the matrix, the reprojection error and ``meets_tolerance``; store nothing.

    Requires ``edit``. 409 when the plan is past ``EDITING`` or has entities (V-08).
    """
    try:
        proposal = propose(db, plan, _input(body), CalibrationTolerances.for_plan(db, plan))
    except (CalibrationConflictError, CalibrationInputError) as exc:
        raise _rejected(db, plan, exc) from None
    return CalibrationProposalOut.model_validate(proposal.to_json())


@router.post(
    "/plans/{plan_id}/calibration/accept",
    dependencies=[Depends(require_csrf)],
    responses=ACCEPT_RESPONSES,
)
def accept_calibration(
    request: Request,
    body: CalibrationAcceptIn,
    plan: Annotated[Plan, Depends(require_plan_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> PlanOut:
    """Store the calibration as the plan's active one and move the plan to ``EDITING``.

    Appends ``calibration.proposed`` and ``calibration.accepted``. ``as_metric`` is
    rejected for ``none`` and above the reprojection tolerance (V-05). Method ``none``
    leaves the plan ``non_metric``. Requires ``edit`` and the plan's edit lease.
    """
    tolerances = CalibrationTolerances.for_plan(db, plan)
    client_ip = request.client.host if request.client else None
    try:
        accept(db, plan, user, _input(body), body.as_metric, tolerances, client_ip)
    except (CalibrationConflictError, CalibrationInputError, MetricRejectedError) as exc:
        raise _rejected(db, plan, exc) from None
    db.commit()
    return plan_out(db, plan)
