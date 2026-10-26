"""The ``suggest_lines`` job: suggested polylines inserted as entities (REQ-17, REQ-15; spec 10).

When it is queued (spec section 9.2), idempotent per plan and calibration content
(:func:`suggest_key`: source, method, matrix, paper positions and paper scale):

- ``none`` (``non_metric``): on acceptance; it runs on the original image.
- other methods: at the end of the ``rectify`` job (:func:`after_rectify`, in the same
  transaction that stores the rectified image), or on acceptance when the rectify job of
  the same image already succeeded. So the job never waits for its input.

Accepting the same calibration again queues nothing while a job with the same key is
queued, running or succeeded. A different calibration (allowed while the plan holds only
pending suggestions, V-08) soft-deletes the pending suggestions and supersedes the
previous jobs (``params.superseded_at``); a superseded job finishes ``succeeded`` without
inserting anything (``params.result.skipped``). :func:`relaunch` does the same on request
while no entity is accepted or manual.

The job decodes the image, runs :func:`sketch2cad.vision.suggest_polylines` with the
parameters of the case's incident type, then, under the plan row lock, converts to the
active calibration's local metres (:mod:`sketch2cad.vision.convert`) and inserts every
polyline in the current draft revision in ONE transaction with ONE ``suggestions.added``
event (``after``: count, entity ids, job, calibration, pipeline version and parameters;
there is no ``entity.created`` per suggestion: the geometry is reproducible from the
stored rectified image, the parameters and the pipeline version, and every later change
carries its own ``before``). Entities: ``polyline`` in ``L_STRUCTURE``, ``review_status``
``suggested``, ``metric_origin`` ``trace_only``, ``source`` ``{cv, job_id, null}``,
``created_by`` null. Failures end ``failed`` with a Spanish ``{cause, action}`` and insert
nothing. Like the rest of the worker, only ``DATABASE_URL`` and ``STORAGE_ROOT`` are read.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from importlib.metadata import version
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import procrastinate
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

import sketch2cad.calibration.rectify as rectify_job
import sketch2cad.worker.app as worker_app
from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.auth.lockout import utc_now
from sketch2cad.config_params import case_incident_type, get_param, get_params
from sketch2cad.db.models import (
    Artifact,
    Calibration,
    CalibrationMethod,
    Entity,
    JobMeta,
    JobStatus,
    MetricOrigin,
    Plan,
    ReviewStatus,
    Revision,
    RevisionState,
    Source,
    User,
)
from sketch2cad.entities.service import EDITABLE_STATUSES
from sketch2cad.geometry import scale_matrix, scan_dpi_scale
from sketch2cad.geometry.frame import LocalFrame
from sketch2cad.jobs.service import (
    TERMINAL_STATUSES,
    JobError,
    QcJobError,
    _finish,  # pyright: ignore[reportPrivateUsage]
    _locked_job,  # pyright: ignore[reportPrivateUsage]
    _start,  # pyright: ignore[reportPrivateUsage]
    decode_image,
)
from sketch2cad.plans.service import active_calibration, current_revision, recompute_metric_status
from sketch2cad.sources.raster import RASTER_DPI
from sketch2cad.storage import ObjectNotFoundError, Storage
from sketch2cad.worker import tasks_raster

logger = logging.getLogger(__name__)

SUGGEST_JOB_KIND = "suggest_lines"
SUGGEST_TASK_NAME = "suggest_lines"
VISION_PIPELINE_VERSION = (
    f"suggest_lines/1 skimage={version('scikit-image')} shapely={version('shapely')} "
    f"opencv={cv2.__version__}"
)
"""``entity.model_version`` and ``job_meta.model_version`` of the suggestions."""

SUGGESTION_LAYER = "L_STRUCTURE"
AI_ASSIST_PARAM = "ai_assist.enabled"
IMAGE_RECTIFIED = "rectified"
IMAGE_ORIGINAL = "original"
NON_METRIC_ASSUMED_DPI = float(RASTER_DPI)
"""``none`` on an image without DPI: assume the 300 dpi of rasterized PDFs (provisional)."""

PENDING_REVIEW_STATUSES = (ReviewStatus.SUGGESTED.value, ReviewStatus.REJECTED.value)
LOCKING_REVIEW_STATUSES = (ReviewStatus.ACCEPTED.value, ReviewStatus.CORRECTED.value)
_ACTIVE_JOB_STATUSES = (JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.SUCCEEDED.value)

LOCKED_MESSAGE = (
    "Ya hay entidades aceptadas o dibujadas a mano en este plano: no se pueden relanzar "
    "las sugerencias."
)
RECTIFIED_PENDING_MESSAGE = (
    "La imagen rectificada aún no está lista. Espera a que termine y vuelve a intentarlo."
)
NO_CALIBRATION_MESSAGE = "Acepta una calibración antes de pedir sugerencias."

RECTIFIED_MISSING = JobError(
    "La imagen rectificada del plano no está disponible.",
    "Vuelve a aceptar la calibración o relanza las sugerencias.",
)
UNREADABLE_INPUT = JobError(
    "No se pudo leer la imagen para sugerir líneas.",
    "Relanza las sugerencias; si el problema continúa, vuelve a cargar la fuente.",
)
IMAGE_UNSUITABLE = JobError(
    "La imagen es demasiado grande o no tiene un formato que se pueda analizar.",
    "Continúa dibujando a mano o usa una fuente de menor resolución.",
)
PLAN_NOT_EDITABLE = JobError(
    "El plano ya no está en edición: no se añadieron sugerencias.",
    "Si las necesitas, reabre el plano y relanza las sugerencias.",
)
INTERNAL_ERROR = JobError(
    "Error interno al sugerir líneas.",
    "Vuelve a intentarlo más tarde; si el problema continúa, contacta al administrador.",
)


class SuggestJobError(Exception):
    """The job cannot produce suggestions; carries the user-facing error."""

    def __init__(self, error: JobError, reason: str) -> None:
        super().__init__(reason)
        self.error = error


class SuggestionsConflictError(Exception):
    """Suggestions cannot be relaunched now (409); ``code`` names the reason."""

    def __init__(
        self, message: str, code: str | None = None, extensions: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extensions = dict(extensions or {})


# --- Rules shared with the calibration -----------------------------------------------------


def locking_entities_exist(db: Session, plan: Plan) -> bool:
    """Whether any revision of ``plan`` has a live accepted, corrected or manual entity.

    Spec section 7 step 8: such an entity locks the calibration (V-08) and the relaunch of
    suggestions; pending (``suggested``) and rejected suggestions do not.
    """
    query = (
        select(Entity.id)
        .join(Revision, Revision.id == Entity.revision_id)
        .where(
            Revision.plan_id == plan.id,
            Entity.deleted_at.is_(None),
            or_(
                Entity.review_status.in_(LOCKING_REVIEW_STATUSES),
                Entity.source["method"].astext == "manual",
            ),
        )
        .limit(1)
    )
    return db.scalar(query) is not None


def suggest_key(plan: Plan, calibration: Calibration) -> str:
    """SHA-256 of what determines the suggestions of ``calibration`` on ``plan``."""
    paper = [p.get("paper_mm") for p in calibration.control_points if p.get("role") == "control"]
    content = {
        "plan_id": str(plan.id),
        "source_id": str(plan.primary_source_id),
        "method": calibration.method,
        "image_to_paper": calibration.image_to_paper,
        "paper_mm": paper,
        "paper_scale": calibration.paper_scale,
        "px_per_mm": rectify_job.PX_PER_MM,
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def ai_assist_enabled(db: Session) -> bool:
    """``ai_assist.enabled`` (environment only; true by default)."""
    return bool(get_param(AI_ASSIST_PARAM, session=db))


def _superseded(job: JobMeta) -> bool:
    return job.params.get("superseded_at") is not None


def supersede_jobs(db: Session, plan: Plan) -> int:
    """Mark every not yet superseded ``suggest_lines`` job of ``plan``; return how many."""
    query = select(JobMeta).where(
        JobMeta.plan_id == plan.id,
        JobMeta.kind == SUGGEST_JOB_KIND,
        JobMeta.params["superseded_at"].astext.is_(None),
    )
    now = utc_now().isoformat()
    jobs = list(db.scalars(query.order_by(JobMeta.id).with_for_update()))
    for job in jobs:
        job.params = {**job.params, "superseded_at": now}
    db.flush()
    return len(jobs)


def clear_pending_suggestions(
    db: Session, plan: Plan, user: User, reason: str, client_ip: str | None = None
) -> int:
    """Soft-delete the pending suggestions of the current revision and supersede the jobs.

    Pending means live, not manual and ``suggested`` or ``rejected``. Writes one
    ``entity.deleted`` event (``entity_id`` null, ``before`` with the ids and count) when
    any is deleted. Runs in the caller's transaction, under its plan row lock.
    """
    supersede_jobs(db, plan)
    if plan.current_revision_id is None:
        return 0
    query = select(Entity).where(
        Entity.revision_id == plan.current_revision_id,
        Entity.deleted_at.is_(None),
        Entity.review_status.in_(PENDING_REVIEW_STATUSES),
        Entity.source["method"].astext != "manual",
    )
    entities = list(db.scalars(query.order_by(Entity.id).with_for_update()))
    if not entities:
        return 0
    now = utc_now()
    for entity in entities:
        entity.deleted_at = entity.updated_at = now
    db.flush()
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=EventKind.ENTITY_DELETED,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=plan.current_revision_id,
        before={"entity_ids": [str(e.id) for e in entities], "count": len(entities)},
        reason=reason,
        client_ip=client_ip,
    )
    recompute_metric_status(db, plan)
    logger.info("Cleared %d pending suggestions of plan %s (%s)", len(entities), plan.id, reason)
    return len(entities)


# --- Enqueue -----------------------------------------------------------------------------


def enqueue_suggest_lines(
    db: Session, plan: Plan, calibration: Calibration, artifact_id: uuid.UUID | None
) -> JobMeta | None:
    """Queue ``suggest_lines`` for ``calibration`` in the caller's transaction.

    ``None`` when ``ai_assist.enabled`` is false. Returns the existing job when one with the
    same :func:`suggest_key` is queued, running or succeeded and not superseded; otherwise
    supersedes the plan's other jobs and adds a ``queued`` one. ``artifact_id`` is the
    rectified image (``None`` for ``none``, which runs on the original).
    """
    if not ai_assist_enabled(db):
        logger.info("AI assist disabled: no suggest_lines job for plan %s", plan.id)
        return None
    key = suggest_key(plan, calibration)
    existing = db.scalars(
        select(JobMeta)
        .where(
            JobMeta.plan_id == plan.id,
            JobMeta.kind == SUGGEST_JOB_KIND,
            JobMeta.params["suggest_key"].astext == key,
            JobMeta.params["superseded_at"].astext.is_(None),
            JobMeta.status.in_(_ACTIVE_JOB_STATUSES),
        )
        .limit(1)
    ).first()
    if existing is not None:
        logger.info("Suggest job %s already covers calibration %s", existing.id, calibration.id)
        return existing
    supersede_jobs(db, plan)
    none = calibration.method == CalibrationMethod.NONE.value
    job = JobMeta(
        id=uuid.uuid4(),
        plan_id=plan.id,
        source_id=plan.primary_source_id,
        kind=SUGGEST_JOB_KIND,
        params={
            "plan_id": str(plan.id),
            "calibration_id": str(calibration.id),
            "source_id": str(plan.primary_source_id),
            "suggest_key": key,
            "image": IMAGE_ORIGINAL if none else IMAGE_RECTIFIED,
            "rectified_artifact_id": None if artifact_id is None else str(artifact_id),
        },
        status=JobStatus.QUEUED.value,
        model_version=VISION_PIPELINE_VERSION,
    )
    db.add(job)
    db.flush()
    connection = db.connection().connection.driver_connection
    worker_app.app.configure_task(SUGGEST_TASK_NAME, connection=connection).defer(
        job_meta_id=str(job.id)
    )
    logger.info("Queued suggest job %s for calibration %s", job.id, calibration.id)
    return job


def rectified_artifact_id(db: Session, plan: Plan, calibration: Calibration) -> uuid.UUID | None:
    """The rectified image of ``calibration`` from its succeeded rectify job, if any."""
    key = rectify_job.rectify_key(calibration, plan.primary_source_id)
    job = db.scalars(
        select(JobMeta)
        .where(
            JobMeta.plan_id == plan.id,
            JobMeta.kind == rectify_job.RECTIFY_JOB_KIND,
            JobMeta.params["rectify_key"].astext == key,
            JobMeta.status == JobStatus.SUCCEEDED.value,
            JobMeta.result_ref.is_not(None),
        )
        .limit(1)
    ).first()
    return None if job is None or job.result_ref is None else uuid.UUID(job.result_ref)


def on_calibration_accepted(
    db: Session,
    plan: Plan,
    user: User,
    previous: Calibration | None,
    calibration: Calibration,
    client_ip: str | None = None,
) -> JobMeta | None:
    """Clear stale suggestions and queue the job of a newly accepted calibration.

    Same content as ``previous`` (same :func:`suggest_key`, no local frame set on it):
    suggestions stay and the existing job is reused. Otherwise the pending suggestions are
    soft-deleted first. ``none`` queues now; other methods queue now only when their
    rectified image already exists, else :func:`after_rectify` does it.
    """
    same = (
        previous is not None
        and previous.local_frame is None
        and suggest_key(plan, previous) == suggest_key(plan, calibration)
    )
    if not same:
        clear_pending_suggestions(db, plan, user, "recalibration", client_ip)
    if calibration.method == CalibrationMethod.NONE.value:
        return enqueue_suggest_lines(db, plan, calibration, None)
    artifact_id = rectified_artifact_id(db, plan, calibration)
    if artifact_id is None:
        return None
    return enqueue_suggest_lines(db, plan, calibration, artifact_id)


def after_rectify(db: Session, job: JobMeta, artifact: Artifact) -> None:
    """Chain ``suggest_lines`` from a succeeded ``rectify`` job, in its transaction.

    Queues only for the plan's active calibration with the same rectified image; a rectify
    job of a replaced calibration queues nothing.
    """
    plan = db.get(Plan, job.plan_id) if job.plan_id is not None else None
    calibration = None if plan is None else active_calibration(db, plan)
    if plan is None or calibration is None or calibration.image_to_paper is None:
        return
    if rectify_job.rectify_key(calibration, plan.primary_source_id) != job.params.get(
        "rectify_key"
    ):
        logger.info("Rectify job %s is not of the active calibration; no suggestions", job.id)
        return
    enqueue_suggest_lines(db, plan, calibration, artifact.id)


def relaunch(db: Session, plan: Plan, user: User, client_ip: str | None = None) -> JobMeta | None:
    """Delete the pending suggestions and queue a new job (``POST /plans/{id}/jobs``).

    The caller has checked that ``user`` may edit the plan (lease included) under the plan
    row lock. Raises :class:`SuggestionsConflictError` when the plan has no calibration,
    has an accepted or manual entity (``suggestions_locked``) or its rectified image is not
    ready (``rectified_image_pending``). ``None`` when AI assist is disabled.
    """
    calibration = active_calibration(db, plan)
    if calibration is None:
        raise SuggestionsConflictError(NO_CALIBRATION_MESSAGE, "calibration_required")
    if locking_entities_exist(db, plan):
        raise SuggestionsConflictError(LOCKED_MESSAGE, "suggestions_locked")
    artifact_id: uuid.UUID | None = None
    if calibration.method != CalibrationMethod.NONE.value:
        artifact_id = rectified_artifact_id(db, plan, calibration)
        if artifact_id is None:
            raise SuggestionsConflictError(RECTIFIED_PENDING_MESSAGE, "rectified_image_pending")
    clear_pending_suggestions(db, plan, user, "suggestions_relaunched", client_ip)
    return enqueue_suggest_lines(db, plan, calibration, artifact_id)


# --- Run ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Inputs:
    pixels: npt.NDArray[Any]
    params: Any  # sketch2cad.vision.SuggestParams, imported lazily
    image_to_paper: npt.NDArray[np.float64]


def _rectified_pixels(db: Session, storage: Storage, job: JobMeta) -> npt.NDArray[Any]:
    raw_id = job.params.get("rectified_artifact_id")
    artifact = db.get(Artifact, uuid.UUID(raw_id)) if raw_id else None
    if artifact is None:
        raise SuggestJobError(RECTIFIED_MISSING, "job has no rectified image")
    try:
        return decode_image(storage.get(artifact.storage_key))
    except ObjectNotFoundError as exc:
        raise SuggestJobError(RECTIFIED_MISSING, "rectified image object is missing") from exc
    except QcJobError as exc:
        raise SuggestJobError(UNREADABLE_INPUT, "rectified image does not decode") from exc


def _original_pixels(
    db: Session, storage: Storage, job: JobMeta
) -> tuple[npt.NDArray[Any], npt.NDArray[np.float64]]:
    """The original resampled to the rectified resolution with an assumed scan scale."""
    source = db.get(Source, job.source_id) if job.source_id is not None else None
    if source is None:
        raise SuggestJobError(UNREADABLE_INPUT, "job has no existing source")
    try:
        with db.begin_nested():
            source_pixels = rectify_job._source_pixels  # pyright: ignore[reportPrivateUsage]
            pixels = source_pixels(db, storage, source)
    except rectify_job.RectifyJobError as exc:
        raise SuggestJobError(exc.error, str(exc)) from exc
    dpi = float(source.dpi) if source.dpi is not None else NON_METRIC_ASSUMED_DPI
    matrix = scale_matrix(scan_dpi_scale(dpi))
    height, width = pixels.shape[:2]
    try:
        size = rectify_job.paper_size_mm("scan_dpi", matrix.tolist(), [], (width, height))
        canvas = rectify_job.rectify_image(pixels, matrix, size)
    except rectify_job.RectifyError as exc:
        raise SuggestJobError(IMAGE_UNSUITABLE, str(exc)) from exc
    return canvas, matrix


def _load_inputs(db: Session, storage: Storage, job: JobMeta) -> _Inputs:
    from sketch2cad.vision import SuggestParams  # heavy: worker only

    plan = db.get(Plan, job.plan_id) if job.plan_id is not None else None
    raw_id = job.params.get("calibration_id")
    calibration = db.get(Calibration, uuid.UUID(raw_id)) if raw_id else None
    if plan is None or calibration is None:
        raise SuggestJobError(rectify_job.MISSING_CALIBRATION, "job has no calibration")
    if job.params.get("image") == IMAGE_ORIGINAL:
        pixels, matrix = _original_pixels(db, storage, job)
    else:
        if calibration.image_to_paper is None:
            raise SuggestJobError(rectify_job.MISSING_CALIBRATION, "calibration has no matrix")
        pixels = _rectified_pixels(db, storage, job)
        matrix = np.asarray(calibration.image_to_paper, dtype=np.float64)
    names = SuggestParams.param_names()
    values = get_params(names, incident_type=case_incident_type(db, plan.case_file_id), session=db)
    return _Inputs(pixels, SuggestParams.from_params(values), matrix)


def _detect(inputs: _Inputs) -> list[Any]:
    from sketch2cad.vision import VisionInputError, suggest_polylines

    try:
        return suggest_polylines(inputs.pixels, inputs.params)
    except VisionInputError as exc:
        raise SuggestJobError(IMAGE_UNSUITABLE, str(exc)) from exc


def _insert(db: Session, plan: Plan, job: JobMeta, inputs: _Inputs, found: list[Any]) -> int:
    """Insert the suggestions and ``suggestions.added``; return the count (plan locked)."""
    from sketch2cad.vision.convert import SuggestionFrame, convert_polyline

    calibration = active_calibration(db, plan)
    revision = current_revision(db, plan)
    assert calibration is not None  # noqa: S101 - checked by the caller
    if (
        revision is None
        or plan.status not in EDITABLE_STATUSES
        or revision.state != RevisionState.DRAFT.value
    ):
        raise SuggestJobError(PLAN_NOT_EDITABLE, "plan is not editable")
    stored = calibration.local_frame
    frame = SuggestionFrame(
        calibration.paper_scale,
        None if stored is None else LocalFrame.from_json(stored),
        inputs.image_to_paper,
    )
    now = utc_now()
    entities: list[Entity] = []
    for polyline in found:
        converted = convert_polyline(polyline.points, frame)
        if converted is None:
            continue
        entities.append(
            Entity(
                id=uuid.uuid4(),
                revision_id=revision.id,
                type="polyline",
                layer=SUGGESTION_LAYER,
                geometry=converted.geometry,
                image_region={"source_id": str(job.source_id), "polygon_px": converted.polygon_px},
                attributes={},
                source={"method": "cv", "job_id": str(job.id), "parent_entity_id": None},
                confidence=float(polyline.confidence),
                model_version=VISION_PIPELINE_VERSION,
                review_status=ReviewStatus.SUGGESTED.value,
                metric_origin=MetricOrigin.TRACE_ONLY.value,
                created_by=None,
                updated_at=now,
            )
        )
    db.add_all(entities)
    db.flush()
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=EventKind.SUGGESTIONS_ADDED,
        actor_id=None,
        actor_role="system",
        plan_id=plan.id,
        revision_id=revision.id,
        entity_id=None,
        after={
            "job_id": str(job.id),
            "count": len(entities),
            "entity_ids": [str(e.id) for e in entities],
            "calibration_id": str(calibration.id),
            "layer": SUGGESTION_LAYER,
            "pipeline_version": VISION_PIPELINE_VERSION,
            "params": asdict(inputs.params),
        },
    )
    recompute_metric_status(db, plan)
    job.params = {
        **job.params,
        "pipeline_params": asdict(inputs.params),
        "result": {"count": len(entities), "revision_id": str(revision.id)},
    }
    return len(entities)


def _skip_reason(db: Session, plan: Plan, job: JobMeta) -> str | None:
    if _superseded(job):
        return "superseded"
    calibration = active_calibration(db, plan)
    if calibration is None or suggest_key(plan, calibration) != job.params.get("suggest_key"):
        return "calibration_changed"
    return None


def _skip(job: JobMeta, reason: str) -> str:
    job.params = {**job.params, "result": {"count": 0, "skipped": reason}}
    _finish(job, JobStatus.SUCCEEDED)
    logger.info("Suggest job %s skipped: %s", job.id, reason)
    return job.status


def _fail(factory: sessionmaker[Session], job_id: uuid.UUID, error: JobError) -> str:
    with factory() as db, db.begin():
        job = _locked_job(db, job_id)
        if job.status not in TERMINAL_STATUSES:
            _finish(job, JobStatus.FAILED, error)
        return job.status


def _locked_plan(db: Session, job_id: uuid.UUID) -> Plan | None:
    plan_id = db.scalar(select(JobMeta.plan_id).where(JobMeta.id == job_id))
    if plan_id is None:
        return None
    return db.scalars(select(Plan).where(Plan.id == plan_id).with_for_update()).first()


def run_suggest_job(factory: sessionmaker[Session], storage: Storage, job_id: uuid.UUID) -> str:
    """Run the ``suggest_lines`` job ``job_id`` and return its final status.

    ``running`` commits first; the image is decoded and analyzed with no lock held; the
    plan row is then locked (before the job row, the order of accept and relaunch) and the
    entities, the event, the metric status and the final status commit together. A
    finished job is left untouched; a superseded job, or one whose calibration changed,
    ends ``succeeded`` with nothing inserted. Expected failures end ``failed`` with a
    Spanish ``{cause, action}``; unexpected errors with :data:`INTERNAL_ERROR`, re-raised.
    """
    if not _start(factory, job_id):
        with factory() as db:
            job = db.get(JobMeta, job_id)
            return job.status if job is not None else JobStatus.FAILED.value
    try:
        with factory() as db:
            job = db.get(JobMeta, job_id)
            if job is None or _superseded(job):
                inputs = None
            else:
                inputs = _load_inputs(db, storage, job)
        found = [] if inputs is None else _detect(inputs)
        with factory() as db, db.begin():
            plan = _locked_plan(db, job_id)
            job = _locked_job(db, job_id)
            if job.status in TERMINAL_STATUSES:  # a concurrent run finished it
                return job.status
            reason = "plan_missing" if plan is None else _skip_reason(db, plan, job)
            if reason is not None or plan is None or inputs is None:
                return _skip(job, reason or "superseded")
            count = _insert(db, plan, job, inputs, found)
            _finish(job, JobStatus.SUCCEEDED)
            logger.info("Suggest job %s inserted %d suggestions", job.id, count)
            return job.status
    except SuggestJobError as exc:
        logger.warning("Suggest job %s failed: %s", job_id, exc)
        return _fail(factory, job_id, exc.error)
    except Exception:
        logger.exception("Suggest job %s failed unexpectedly", job_id)
        _fail(factory, job_id, INTERNAL_ERROR)
        raise


def suggest_lines(job_meta_id: str) -> str:
    """Procrastinate task body; returns the final ``job_meta`` status."""
    logger.info("Suggest job %s started", job_meta_id)
    return run_suggest_job(
        tasks_raster._session_factory(),  # pyright: ignore[reportPrivateUsage]
        tasks_raster._storage(),  # pyright: ignore[reportPrivateUsage]
        uuid.UUID(job_meta_id),
    )


def register(app: procrastinate.App) -> None:
    """Register :data:`SUGGEST_TASK_NAME` on ``app``."""
    app.task(name=SUGGEST_TASK_NAME)(suggest_lines)
