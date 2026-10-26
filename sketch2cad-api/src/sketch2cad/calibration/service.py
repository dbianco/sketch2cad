"""Calibration proposal and acceptance (spec sections 11.1, 11.2, 11.4, 14.1; REQ-03, US-03).

A proposal is computed from the request alone and persists nothing. Acceptance takes the
same inputs again, recomputes the proposal (the server never trusts a client-side matrix)
and stores it as the single active calibration of the plan.

Reprojection error (spec section 11.2): an exact 4-point fit reproduces its own control
points, so their error is ~0. Extra check points, marked by the technician with a known
paper position, do not take part in the fit; when any are sent, ``reprojection_error_mm``
is their max and RMS distance and it decides ``meets_tolerance``. Without check points
the error is that of the control points of a homography, and ``null`` for scan methods.
"""

import logging
import math
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

# Module import: tasks_suggest imports calibration.rectify, whose package imports this module.
import sketch2cad.worker.tasks_suggest as suggest_jobs
from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.calibration.rectify import enqueue_rectify
from sketch2cad.config import Settings
from sketch2cad.config_params import case_incident_type, code_default, get_params
from sketch2cad.db.models import (
    Calibration,
    CalibrationMethod,
    IncidentType,
    Plan,
    PlanStatus,
    Source,
    SourceMode,
    User,
)
from sketch2cad.geometry import (
    Matrix3,
    Point,
    ReprojectionError,
    apply_h,
    homography_from_4_points,
    homography_to_json,
    rectangle_corners_mm,
    reprojection_error,
    scale_matrix,
    scan_dpi_scale,
    scan_segment_scale,
)
from sketch2cad.plans.lease import LeaseConflictError, ensure_lease_holder
from sketch2cad.plans.service import active_calibration, recompute_metric_status

logger = logging.getLogger(__name__)

CALIBRATABLE_STATUSES = frozenset({PlanStatus.CALIBRATION_PENDING, PlanStatus.EDITING})
"""A plan can be (re)calibrated only before review; V-08 then requires no accepted entity."""

V08_MESSAGE = "No puedes recalibrar un plano con entidades. Descártalas o crea otro plano."
V05_MESSAGE = (
    "El error de calibración es de {error} mm y supera el máximo de {maximum} mm. "
    "Repite los puntos o continúa como aproximado."
)

ROLE_CONTROL = "control"
ROLE_CHECK = "check"
BASIS_CONTROL = "control_points"
BASIS_CHECK = "check_points"
MAX_REPROJECTION_ERROR = "calibration.max_reprojection_error_mm"
MAX_SCALE_DISCREPANCY = "calibration.max_scale_discrepancy_pct"


@dataclass(frozen=True)
class CalibrationTolerances:
    """Tolerances of spec section 11.4; defaults are the registry code defaults."""

    max_reprojection_error_mm: float = code_default(MAX_REPROJECTION_ERROR)
    max_scale_discrepancy_pct: float = code_default(MAX_SCALE_DISCREPANCY)

    @classmethod
    def from_settings(cls, settings: Settings) -> "CalibrationTolerances":
        """Read the tolerances from the ``CALIBRATION_*`` settings."""
        return cls(
            max_reprojection_error_mm=settings.CALIBRATION_MAX_REPROJECTION_ERROR_MM,
            max_scale_discrepancy_pct=settings.CALIBRATION_MAX_SCALE_DISCREPANCY_PCT,
        )

    @classmethod
    def resolve(
        cls, session: Session | None, incident_type: IncidentType | None
    ) -> "CalibrationTolerances":
        """Resolve both tolerances for ``incident_type`` (overrides, then the default)."""
        values = get_params(
            (MAX_REPROJECTION_ERROR, MAX_SCALE_DISCREPANCY),
            incident_type=incident_type,
            session=session,
        )
        return cls(float(values[MAX_REPROJECTION_ERROR]), float(values[MAX_SCALE_DISCREPANCY]))

    @classmethod
    def for_plan(cls, session: Session, plan: Plan) -> "CalibrationTolerances":
        """Tolerances for the incident type of the plan's case file."""
        return cls.resolve(session, case_incident_type(session, plan.case_file_id))


class CalibrationInputError(ValueError):
    """The calibration inputs are inconsistent with the method or degenerate (422)."""


class CalibrationConflictError(Exception):
    """The plan cannot be calibrated now: wrong status, it has entities or no lease (409).

    ``code`` is the PRD validation rule (``V-08``, ``V-12``) or ``lease_required`` when one
    applies; ``extensions`` are extra problem details members (the lease ``holder``).
    """

    def __init__(
        self, message: str, code: str | None = None, extensions: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extensions = dict(extensions or {})


class MetricRejectedError(ValueError):
    """``as_metric`` was requested for a calibration that cannot be metric (422).

    ``code`` is the PRD validation rule (``V-05``) when one applies; ``extensions`` holds
    the values behind the message.
    """

    def __init__(
        self, message: str, code: str | None = None, extensions: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extensions = extensions or {}


@dataclass(frozen=True)
class PointInput:
    """A marked point: image pixel and, when known, its paper position in mm."""

    image_px: Point
    paper_mm: Point | None = None


@dataclass(frozen=True)
class CalibrationInput:
    """The inputs of ``PUT /plans/{id}/calibration`` and ``POST .../calibration/accept``."""

    method: CalibrationMethod
    control_points: tuple[PointInput, ...] = ()
    check_points: tuple[PointInput, ...] = ()
    known_dimensions_mm: tuple[float, float] | None = None
    segment_mm: float | None = None
    paper_scale: int | None = None


@dataclass(frozen=True)
class ScaleInfo:
    """Uniform scan scale and, when the source has a DPI, the segment's difference with it."""

    mm_per_px: float
    dpi: float | None
    dpi_discrepancy_pct: float | None
    within_tolerance: bool | None

    def to_json(self) -> dict[str, Any]:
        return {
            "mm_per_px": self.mm_per_px,
            "dpi": self.dpi,
            "dpi_discrepancy_pct": self.dpi_discrepancy_pct,
            "within_tolerance": self.within_tolerance,
        }


@dataclass(frozen=True)
class Proposal:
    """A computed calibration; ``points`` is the ``calibration.control_points`` JSON."""

    method: CalibrationMethod
    matrix: Matrix3 | None
    points: list[dict[str, Any]]
    error: ReprojectionError | None
    error_basis: str | None
    meets_tolerance: bool
    max_reprojection_error_mm: float
    paper_scale: int | None
    scale: ScaleInfo | None = None

    @property
    def image_to_paper(self) -> list[list[float]] | None:
        """JSON form of the 3x3 matrix (``calibration.image_to_paper``)."""
        return None if self.matrix is None else homography_to_json(self.matrix)

    def to_json(self) -> dict[str, Any]:
        """JSON form returned by the API and stored in ``calibration.proposed``."""
        return {
            "method": self.method.value,
            "image_to_paper": self.image_to_paper,
            "control_points": self.points,
            "reprojection_error_mm": None if self.error is None else self.error.to_json(),
            "reprojection_error_basis": self.error_basis,
            "meets_tolerance": self.meets_tolerance,
            "max_reprojection_error_mm": self.max_reprojection_error_mm,
            "paper_scale": self.paper_scale,
            "scale": None if self.scale is None else self.scale.to_json(),
        }


def _point_json(
    role: str, image_px: Point, paper_mm: Point | None, error_mm: float | None
) -> dict[str, Any]:
    return {
        "role": role,
        "image_px": [float(image_px[0]), float(image_px[1])],
        "paper_mm": None if paper_mm is None else [float(paper_mm[0]), float(paper_mm[1])],
        "error_mm": error_mm,
    }


def _homography(data: CalibrationInput) -> tuple[Matrix3, list[Point], list[Point]]:
    if len(data.control_points) != 4:
        raise CalibrationInputError("photo_homography_4pt needs exactly 4 control points.")
    dimensions = data.known_dimensions_mm
    corners = None if dimensions is None else rectangle_corners_mm(*dimensions)
    image = [p.image_px for p in data.control_points]
    paper: list[Point] = []
    for index, point in enumerate(data.control_points):
        target = point.paper_mm
        if target is None and corners is not None:
            target = corners[index]
        if target is None:
            raise CalibrationInputError(
                "Each control point needs paper_mm unless known_dimensions_mm is given."
            )
        paper.append(target)
    return homography_from_4_points(image, paper), image, paper


def _scan_scale(
    data: CalibrationInput, dpi: float | None, tolerances: CalibrationTolerances
) -> tuple[ScaleInfo, list[Point]]:
    if data.method is CalibrationMethod.SCAN_DPI:
        if dpi is None:
            raise CalibrationInputError("The source has no DPI metadata; use scan_segment.")
        return ScaleInfo(scan_dpi_scale(dpi), dpi, None, None), []
    if len(data.control_points) != 2 or data.segment_mm is None:
        raise CalibrationInputError("scan_segment needs exactly 2 control points and segment_mm.")
    a, b = data.control_points[0].image_px, data.control_points[1].image_px
    segment = scan_segment_scale(a, b, data.segment_mm, dpi)
    discrepancy = segment.dpi_discrepancy_pct
    limit = tolerances.max_scale_discrepancy_pct
    within = None if discrepancy is None else abs(discrepancy) <= limit
    return ScaleInfo(segment.mm_per_px, dpi, discrepancy, within), [a, b]


def _check_errors(matrix: Matrix3, checks: tuple[PointInput, ...]) -> list[float]:
    errors: list[float] = []
    for point in checks:
        if point.paper_mm is None:
            raise CalibrationInputError("Each check point needs paper_mm.")
        mapped = apply_h(matrix, point.image_px)
        errors.append(math.dist(mapped, point.paper_mm))
    return errors


def compute_proposal(
    data: CalibrationInput,
    source_mode: SourceMode,
    source_dpi: float | None,
    tolerances: CalibrationTolerances,
) -> Proposal:
    """Compute the image-to-paper matrix, its error and whether it meets the tolerance.

    Raises :class:`CalibrationInputError` when the inputs do not fit the method or define
    no transform.
    """
    if data.paper_scale is not None and source_mode is SourceMode.SCHEMATIC_SKETCH:
        raise CalibrationInputError("paper_scale applies only to scaled_drawing plans.")
    limit = tolerances.max_reprojection_error_mm
    if data.method is CalibrationMethod.NONE:
        if data.control_points or data.check_points:
            raise CalibrationInputError("Method none takes no points.")
        return Proposal(data.method, None, [], None, None, False, limit, data.paper_scale)
    try:
        scale: ScaleInfo | None = None
        error: ReprojectionError | None = None
        basis: str | None = None
        if data.method is CalibrationMethod.PHOTO_HOMOGRAPHY_4PT:
            matrix, image, paper = _homography(data)
            error, basis = reprojection_error(matrix, image, paper), BASIS_CONTROL
            residuals: list[float | None] = [
                math.dist(apply_h(matrix, i), p) for i, p in zip(image, paper, strict=True)
            ]
        else:
            scale, image = _scan_scale(data, source_dpi, tolerances)
            matrix = scale_matrix(scale.mm_per_px)
            paper = [apply_h(matrix, i) for i in image]
            residuals = [None] * len(image)
        points = [
            _point_json(ROLE_CONTROL, i, p, r)
            for i, p, r in zip(image, paper, residuals, strict=True)
        ]
        check_errors = _check_errors(matrix, data.check_points)
    except CalibrationInputError:
        raise
    except ValueError as exc:  # DegenerateControlPointsError, TransformError, bad scales
        raise CalibrationInputError(str(exc)) from exc
    for point, distance in zip(data.check_points, check_errors, strict=True):
        points.append(_point_json(ROLE_CHECK, point.image_px, point.paper_mm, distance))
    if check_errors:
        rms = math.sqrt(sum(d * d for d in check_errors) / len(check_errors))
        error, basis = ReprojectionError(max(check_errors), rms), BASIS_CHECK
    meets = error is None or error.max_mm <= limit
    return Proposal(
        data.method, matrix, points, error, basis, meets, limit, data.paper_scale, scale
    )


def _source_dpi(db: Session, plan: Plan) -> float | None:
    source = db.get(Source, plan.primary_source_id)
    return None if source is None or source.dpi is None else float(source.dpi)


def has_entities(db: Session, plan: Plan) -> bool:
    """Whether any revision of ``plan`` holds a live accepted, corrected or manual entity.

    Pending suggestions (``suggested``, ``rejected``) do not lock the calibration (spec
    section 7 step 8); :func:`accept` deletes them when the calibration changes.
    """
    return suggest_jobs.locking_entities_exist(db, plan)


def ensure_calibratable(db: Session, plan: Plan) -> None:
    """Raise :class:`CalibrationConflictError` unless ``plan`` may be calibrated now."""
    if plan.status not in CALIBRATABLE_STATUSES:
        raise CalibrationConflictError(
            f"A plan in status {plan.status.value} cannot be calibrated."
        )
    if has_entities(db, plan):
        raise CalibrationConflictError(V08_MESSAGE, code="V-08")


def propose(
    db: Session, plan: Plan, data: CalibrationInput, tolerances: CalibrationTolerances
) -> Proposal:
    """Check the plan can be calibrated and compute the proposal; writes nothing."""
    ensure_calibratable(db, plan)
    return compute_proposal(data, plan.source_mode, _source_dpi(db, plan), tolerances)


def _format_mm(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _ensure_metric_allowed(plan: Plan, proposal: Proposal) -> None:
    if proposal.method is CalibrationMethod.NONE:
        raise MetricRejectedError("A calibration with method none cannot be metric.")
    if not proposal.meets_tolerance and proposal.error is not None:
        message = V05_MESSAGE.format(
            error=_format_mm(proposal.error.max_mm),
            maximum=_format_mm(proposal.max_reprojection_error_mm),
        )
        raise MetricRejectedError(
            message,
            code="V-05",
            extensions={
                "reprojection_error_mm": proposal.error.to_json(),
                "max_reprojection_error_mm": proposal.max_reprojection_error_mm,
            },
        )
    if plan.source_mode is SourceMode.SCALED_DRAWING and proposal.paper_scale is None:
        raise MetricRejectedError("A metric calibration of a scaled_drawing needs paper_scale.")


def accept(
    db: Session,
    plan: Plan,
    user: User,
    data: CalibrationInput,
    as_metric: bool,
    tolerances: CalibrationTolerances,
    client_ip: str | None = None,
) -> Calibration:
    """Persist the recomputed proposal as the plan's only active calibration.

    Locks the plan row, rejects a plan that cannot be calibrated or whose edit lease
    ``user`` does not hold (409) and ``as_metric``
    when it is not allowed (422), deactivates the previous calibration, moves the plan from
    ``CALIBRATION_PENDING`` to ``EDITING`` and appends ``calibration.proposed`` and
    ``calibration.accepted``. Queues the ``rectify`` job (idempotent on the calibration
    content, none for ``none``). When the content changes, the pending suggestions of the
    previous calibration are soft-deleted; ``suggest_lines`` is queued now for ``none`` or
    an already rectified image, else by the rectify job
    (:func:`sketch2cad.worker.tasks_suggest.on_calibration_accepted`). Runs in the caller's
    transaction; the caller commits.
    """
    db.refresh(plan, with_for_update=True)
    proposal = propose(db, plan, data, tolerances)
    try:
        ensure_lease_holder(db, plan, user)
    except LeaseConflictError as exc:
        raise CalibrationConflictError(str(exc), exc.code, exc.extensions) from None
    if as_metric:
        _ensure_metric_allowed(plan, proposal)
    previous = active_calibration(db, plan)
    if previous is not None:
        previous.is_active = False
        db.flush()
    calibration = Calibration(
        plan_id=plan.id,
        method=proposal.method.value,
        image_to_paper=proposal.image_to_paper,
        control_points=proposal.points,
        reprojection_error_mm=None if proposal.error is None else proposal.error.to_json(),
        paper_scale=proposal.paper_scale,
        as_metric=as_metric,
        accepted_by=user.id,
    )
    db.add(calibration)
    before = {"status": plan.status.value, "metric_status": plan.metric_status.value}
    if plan.status is PlanStatus.CALIBRATION_PENDING:
        plan.status = PlanStatus.EDITING
    recompute_metric_status(db, plan)
    db.refresh(calibration)
    _append(
        db,
        plan,
        user,
        calibration.id,
        EventKind.CALIBRATION_PROPOSED,
        client_ip,
        after=proposal.to_json(),
    )
    _append(
        db,
        plan,
        user,
        calibration.id,
        EventKind.CALIBRATION_ACCEPTED,
        client_ip,
        before=before,
        after={
            "calibration_id": str(calibration.id),
            "previous_calibration_id": None if previous is None else str(previous.id),
            "method": proposal.method.value,
            "as_metric": as_metric,
            "status": plan.status.value,
            "metric_status": plan.metric_status.value,
        },
    )
    logger.info(
        "User %s accepted calibration %s (%s, as_metric=%s) of plan %s",
        user.id,
        calibration.id,
        proposal.method.value,
        as_metric,
        plan.id,
    )
    enqueue_rectify(db, plan, calibration)
    suggest_jobs.on_calibration_accepted(db, plan, user, previous, calibration, client_ip)
    return calibration


def _append(
    db: Session,
    plan: Plan,
    user: User,
    calibration_id: uuid.UUID,
    kind: EventKind,
    client_ip: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=kind,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=plan.current_revision_id,
        entity_id=calibration_id,
        before=before,
        after=after,
        client_ip=client_ip,
    )
