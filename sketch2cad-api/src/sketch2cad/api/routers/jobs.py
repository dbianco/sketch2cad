"""Job status endpoint, polled by the client every 2 s (REQ-02, REQ-15), the relaunch of
``suggest_lines`` on a plan (REQ-17) and the ``ocr_crop`` job of a user crop (REQ-18)."""

import logging
import uuid
from http import HTTPStatus
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api.deps import (
    get_audit_session_factory,
    get_current_user,
    require_csrf,
    require_plan_permission,
    require_source_permission,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.config_params import ai_assist_enabled
from sketch2cad.db.models import JobMeta, JobStatus, Plan, PlanStatus, Source, User
from sketch2cad.db.session import get_session
from sketch2cad.entities.service import EntityConflictError, ensure_editable
from sketch2cad.ocr.trocr import CropError, validate_box
from sketch2cad.plans.service import current_revision
from sketch2cad.worker.tasks_ocr import OCR_JOB_KIND, enqueue_ocr_crop
from sketch2cad.worker.tasks_suggest import (
    NO_CALIBRATION_MESSAGE,
    SuggestionsConflictError,
    relaunch,
)

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["jobs"])

JOB_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``read`` on the job's case file (access.denied)."},
    404: {"description": "The job does not exist."},
}


class JobErrorOut(BaseModel):
    """Why the job failed and what to do, in Spanish (REQ-15)."""

    cause: str
    action: str


class OcrResultOut(BaseModel):
    """Reading of an ``ocr_crop`` job (spec section 10).

    ``value_m`` is null when the text is not a usable distance (``reason``: ``empty``,
    ``non_numeric``, ``non_positive`` or ``ambiguous``). ``preselect`` is true when there
    is a value and ``confidence >= min_confidence`` (``ocr.min_confidence``).
    """

    text: str
    raw_text: str
    value_m: float | None
    unit_detected: str | None
    reason: str | None
    confidence: float
    preselect: bool
    min_confidence: float
    model_version: str


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    source_id: uuid.UUID | None
    error: JobErrorOut | None
    result_ref: str | None
    result: OcrResultOut | None = None
    """Set only for a succeeded ``ocr_crop`` job."""


PLAN_JOB_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``edit`` on the plan's case file (access.denied)."},
    404: {"description": "The plan does not exist, or AI assist is disabled."},
    409: {
        "description": "The plan is not editable or the user does not hold its edit lease "
        "(``V-10``, ``V-12`` with ``holder``, ``lease_required``); it has no calibration "
        "(``calibration_required``); an entity is accepted or manual "
        "(``suggestions_locked``); or its rectified image is not ready "
        "(``rectified_image_pending``).",
    },
    422: {
        "description": "Invalid body: unknown kind, non-empty ``suggest_lines`` params; for "
        "``ocr_crop`` a source that is not the plan's primary source (``ocr_source_invalid``) "
        "or a box outside the image or of an invalid size (``ocr_box_invalid``).",
    },
}


class PlanJobIn(BaseModel):
    """``suggest_lines`` on a plan. Its parameters come from the configuration
    (``suggest_lines.*``), so ``params`` must be empty."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["suggest_lines"]
    params: dict[str, Any] = Field(default_factory=dict, max_length=0)


class OcrCropParams(BaseModel):
    """``box_px = [x0, y0, x1, y1]``: pixels of the original image of ``source_id`` (its
    300 dpi raster for a PDF), ``x0 < x1``, ``y0 < y1``, sides from 8 to 2000 px."""

    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    box_px: Annotated[list[StrictInt], Field(min_length=4, max_length=4)]


class OcrCropJobIn(BaseModel):
    """OCR of a crop of the plan's primary source (REQ-18)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ocr_crop"]
    params: OcrCropParams


PlanJobBody = Annotated[PlanJobIn | OcrCropJobIn, Field(discriminator="kind")]


def job_out(job: JobMeta) -> JobOut:
    """API form of a ``job_meta`` row."""
    error = JobErrorOut.model_validate(job.error) if job.error is not None else None
    result = None
    if job.kind == OCR_JOB_KIND and job.status == JobStatus.SUCCEEDED.value:
        result = OcrResultOut.model_validate(job.params.get("result"))
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,  # pyright: ignore[reportArgumentType]
        source_id=job.source_id,
        error=error,
        result_ref=job.result_ref,
        result=result,
    )


def _job_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The job does not exist.")


def _readable_job(
    job_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
) -> JobMeta:
    """The ``{job_id}`` job if the user holds ``read`` on its source's case file.

    Unknown or malformed ids, and jobs without a source, give 404. Denials are 403 with
    ``access.denied``, exactly as for the source itself.
    """
    try:
        job = db.get(JobMeta, uuid.UUID(job_id))
    except ValueError:
        raise _job_not_found() from None
    if job is None or job.source_id is None:
        raise _job_not_found()
    check = require_source_permission("read")
    check(str(job.source_id), request, db, user, audit_factory)
    return job


@router.get("/jobs/{job_id}", responses=JOB_RESPONSES)
def get_job(job: Annotated[JobMeta, Depends(_readable_job)]) -> JobOut:
    """Status of a job; ``error {cause, action}`` is present only when it failed.

    Requires ``read`` on the case file of the job's source.
    """
    return job_out(job)


def _ai_assist_disabled() -> ProblemError:
    return ProblemError(404, "Not Found", "AI assist is disabled.")


def require_ai_assist(db: Session) -> None:
    """Raise 404 when ``ai_assist.enabled`` resolves to false (REQ-19).

    The single gate of every AI job kind (``suggest_lines``, ``ocr_crop``). The value is
    read on each request (global override, else ``AI_ASSIST_ENABLED``), so an admin change
    through ``PUT /config/params/ai_assist.enabled`` applies without a restart.
    """
    if not ai_assist_enabled(db):
        logger.info("Rejected an AI job: AI assist is disabled")
        raise _ai_assist_disabled()


def _conflict(db: Session, plan_id: uuid.UUID, exc: Exception) -> ProblemError:
    db.rollback()
    code: str | None = getattr(exc, "code", None)
    extensions: dict[str, Any] = dict(getattr(exc, "extensions", {}))
    logger.warning("Rejected suggest_lines relaunch on plan %s: %s", plan_id, code)
    if code is not None:
        extensions["code"] = code
    return ProblemError(
        HTTPStatus.CONFLICT, HTTPStatus.CONFLICT.phrase, str(exc), extensions=extensions
    )


@router.post(
    "/plans/{plan_id}/jobs",
    status_code=202,
    dependencies=[Depends(require_csrf)],
    responses=PLAN_JOB_RESPONSES,
)
def post_plan_job(
    request: Request,
    body: PlanJobBody,
    plan: Annotated[Plan, Depends(require_plan_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> JobOut:
    """Queue a job on the plan (202); poll ``GET /jobs/{id}``. Requires ``edit``.

    ``suggest_lines`` (relaunch): allowed while no entity of the plan is accepted,
    corrected or manual, with the plan's edit lease. Soft-deletes the pending suggestions
    (one ``entity.deleted`` event with their ids), supersedes earlier jobs and queues a new
    one; it inserts its suggestions with ``suggestions.added``.

    ``ocr_crop``: reads the number inside ``box_px`` of the plan's primary source; needs
    neither the lease nor a calibration and writes nothing but the job. The succeeded job
    carries ``result`` (:class:`OcrResultOut`).

    Both kinds give 404 when ``ai_assist.enabled`` is false (``GET /config/features``).
    """
    plan_id = plan.id
    require_ai_assist(db)
    if isinstance(body, OcrCropJobIn):
        return _post_ocr_crop(db, plan, user, body.params)
    revision = current_revision(db, plan)
    client_ip = request.client.host if request.client else None
    try:
        if revision is None:  # pragma: no cover - every plan is created with revision 1
            raise SuggestionsConflictError("The plan has no current revision.")
        if plan.status is PlanStatus.CALIBRATION_PENDING:
            raise SuggestionsConflictError(NO_CALIBRATION_MESSAGE, "calibration_required")
        ensure_editable(db, revision, user)
        job = relaunch(db, plan, user, client_ip)
    except (EntityConflictError, SuggestionsConflictError) as exc:
        raise _conflict(db, plan_id, exc) from None
    if job is None:  # pragma: no cover - the switch was checked above
        raise _ai_assist_disabled()
    db.commit()
    logger.info("User %s relaunched suggest_lines on plan %s: job %s", user.id, plan_id, job.id)
    return job_out(job)


def _invalid_ocr(detail: str, code: str) -> ProblemError:
    return ProblemError(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        HTTPStatus.UNPROCESSABLE_ENTITY.phrase,
        detail,
        extensions={"code": code},
    )


def _post_ocr_crop(db: Session, plan: Plan, user: User, params: OcrCropParams) -> JobOut:
    """Validate the crop against the primary source and queue ``ocr_crop``.

    The box is checked against the recorded ``width_px``/``height_px``; a PDF not yet
    rasterized has none, and then only the job checks it.
    """
    source = db.get(Source, params.source_id)
    if source is None or source.id != plan.primary_source_id:
        raise _invalid_ocr("source_id must be the plan's primary source.", "ocr_source_invalid")
    if source.width_px is not None and source.height_px is not None:
        try:
            validate_box(params.box_px, source.width_px, source.height_px)
        except CropError as exc:
            raise _invalid_ocr(str(exc), "ocr_box_invalid") from None
    job = enqueue_ocr_crop(db, plan, source, params.box_px, user.id)
    db.commit()
    logger.info("User %s queued OCR job %s on plan %s", user.id, job.id, plan.id)
    return job_out(job)
