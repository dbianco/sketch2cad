"""The ``ocr_crop`` job: TrOCR reading of a box marked by the user (REQ-18, US-13; spec 10).

``POST /plans/{id}/jobs {kind: "ocr_crop", params: {source_id, box_px}}`` queues it through
:func:`enqueue_ocr_crop`. ``box_px = [x0, y0, x1, y1]`` is in pixels of the ORIGINAL image
of the plan's primary source (of its 300 dpi raster for PDFs, the pixels the viewer
shows); it does not depend on the calibration. Nothing is written but the job row: no
entity and no event. The technician then accepts or corrects the value; a dimension
created from it carries ``ocr_job_id`` (:mod:`sketch2cad.entities.service`), which records
the acceptance.

The job decodes the source, runs :func:`sketch2cad.ocr.trocr.ocr_crop` with no database
transaction open and stores ``params.result`` (:meth:`OcrResult.to_dict`, with
``preselect`` from ``ocr.min_confidence`` of the case's incident type). Failures end
``failed`` with a Spanish ``{cause, action}``; the model is never downloaded here (the
worker image runs offline), so a missing model is a failed job, not a retry.
"""

import io
import logging
import threading
import uuid
from collections.abc import Sequence
from typing import Any

import numpy as np
import procrastinate
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

import sketch2cad.worker.app as worker_app
from sketch2cad.config_params import case_incident_type, get_param
from sketch2cad.db.models import JobMeta, JobStatus, MediaType, Plan, Source
from sketch2cad.jobs.service import (
    MISSING_OBJECT,
    MISSING_SOURCE,
    TERMINAL_STATUSES,
    UNREADABLE_IMAGE,
    UNREADABLE_PDF,
    JobError,
    _finish,  # pyright: ignore[reportPrivateUsage]
    _locked_job,  # pyright: ignore[reportPrivateUsage]
    _start,  # pyright: ignore[reportPrivateUsage]
)
from sketch2cad.ocr import trocr
from sketch2cad.sources.raster import RasterError, rasterize_source
from sketch2cad.storage import ObjectNotFoundError, Storage
from sketch2cad.worker import tasks_raster

logger = logging.getLogger(__name__)

OCR_JOB_KIND = trocr.OCR_JOB_KIND
OCR_TASK_NAME = "ocr_crop"
MIN_CONFIDENCE_PARAM = "ocr.min_confidence"

BOX_OUTSIDE = JobError(
    "El recorte queda fuera de la imagen o tiene un tamaño no válido.",
    "Marca de nuevo el recorte sobre la cota, dentro de la imagen.",
)
MODEL_UNAVAILABLE = JobError(
    "El reconocimiento de texto (OCR) no está disponible en este servidor.",
    "Escribe el valor de la cota a mano; avisa al administrador para que revise el worker.",
)
INTERNAL_ERROR = JobError(
    "Error interno al leer el recorte.",
    "Vuelve a intentarlo o escribe el valor a mano; si continúa, contacta al administrador.",
)


class OcrJobError(Exception):
    """The job cannot read its crop; carries the user-facing error."""

    def __init__(self, error: JobError, reason: str) -> None:
        super().__init__(reason)
        self.error = error


def enqueue_ocr_crop(
    db: Session, plan: Plan, source: Source, box_px: Sequence[int], user_id: uuid.UUID
) -> JobMeta:
    """Add a ``queued`` job and defer its task in the caller's transaction."""
    job = JobMeta(
        id=uuid.uuid4(),
        plan_id=plan.id,
        source_id=source.id,
        kind=OCR_JOB_KIND,
        params={
            "plan_id": str(plan.id),
            "source_id": str(source.id),
            "box_px": list(box_px),
            "requested_by": str(user_id),
        },
        status=JobStatus.QUEUED.value,
        model_version=trocr.OCR_MODEL_VERSION,
    )
    db.add(job)
    db.flush()
    connection = db.connection().connection.driver_connection
    worker_app.app.configure_task(OCR_TASK_NAME, connection=connection).defer(
        job_meta_id=str(job.id)
    )
    logger.info("Queued OCR job %s on source %s", job.id, source.id)
    return job


def decode_rgb(data: bytes) -> Image.Image:
    """Fully decoded 8-bit RGB image; high-depth grey is scaled to 8 bits first."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode.startswith("I;16") or image.mode in {"I", "F"}:
                pixels = np.asarray(image, dtype=np.float64)
                top = float(pixels.max()) or 1.0
                return Image.fromarray((pixels * (255.0 / top)).astype(np.uint8)).convert("RGB")
            return image.convert("RGB")
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise OcrJobError(UNREADABLE_IMAGE, "image does not decode") from exc


def _load_image(db: Session, storage: Storage, job: JobMeta) -> Image.Image:
    source = db.get(Source, job.source_id) if job.source_id is not None else None
    if source is None:
        raise OcrJobError(MISSING_SOURCE, "job has no existing source")
    try:
        with db.begin_nested():
            if source.media_type == MediaType.PDF.value:
                source = rasterize_source(db, storage, source.id)
            data = storage.get(source.raster_key or source.storage_key)
    except RasterError as exc:
        raise OcrJobError(UNREADABLE_PDF, "PDF does not rasterize") from exc
    except ObjectNotFoundError as exc:
        raise OcrJobError(MISSING_OBJECT, "stored object is missing") from exc
    return decode_rgb(data)


def _min_confidence(db: Session, job: JobMeta) -> float:
    plan = db.get(Plan, job.plan_id) if job.plan_id is not None else None
    incident = None if plan is None else case_incident_type(db, plan.case_file_id)
    return float(get_param(MIN_CONFIDENCE_PARAM, incident_type=incident, session=db))


def _fail(factory: sessionmaker[Session], job_id: uuid.UUID, error: JobError) -> str:
    with factory() as db, db.begin():
        job = _locked_job(db, job_id)
        if job.status not in TERMINAL_STATUSES:
            _finish(job, JobStatus.FAILED, error)
        return job.status


def run_ocr_job(factory: sessionmaker[Session], storage: Storage, job_id: uuid.UUID) -> str:
    """Run the ``ocr_crop`` job ``job_id`` and return its final status.

    ``running`` commits first; the image is read and recognized with no transaction
    open; the result and the final status commit together. A finished job is left
    untouched. Unexpected errors end ``failed`` with :data:`INTERNAL_ERROR`, re-raised.
    """
    if not _start(factory, job_id):
        with factory() as db:
            job = db.get(JobMeta, job_id)
            return job.status if job is not None else JobStatus.FAILED.value
    try:
        with factory() as db, db.begin():  # commits a raster made for a PDF source
            job = db.get(JobMeta, job_id)
            if job is None:  # pragma: no cover - _start found it
                return JobStatus.FAILED.value
            box: list[Any] = list(job.params.get("box_px") or [])
            min_confidence = _min_confidence(db, job)
            image = _load_image(db, storage, job)
        try:
            result = trocr.ocr_crop(image, box)
        except trocr.CropError as exc:
            raise OcrJobError(BOX_OUTSIDE, str(exc)) from exc
        except trocr.ModelUnavailableError as exc:
            raise OcrJobError(MODEL_UNAVAILABLE, str(exc)) from exc
        with factory() as db, db.begin():
            job = _locked_job(db, job_id)
            if job.status in TERMINAL_STATUSES:  # a concurrent run finished it
                return job.status
            job.params = {**job.params, "result": result.to_dict(min_confidence)}
            _finish(job, JobStatus.SUCCEEDED)
            found = result.value_m is not None
            logger.info("OCR job %s succeeded (value found: %s)", job.id, found)
            return job.status
    except OcrJobError as exc:
        logger.warning("OCR job %s failed: %s", job_id, exc)
        return _fail(factory, job_id, exc.error)
    except Exception:
        logger.exception("OCR job %s failed unexpectedly", job_id)
        _fail(factory, job_id, INTERNAL_ERROR)
        raise


def ocr_crop(job_meta_id: str) -> str:
    """Procrastinate task body; returns the final ``job_meta`` status."""
    logger.info("OCR job %s started", job_meta_id)
    return run_ocr_job(
        tasks_raster._session_factory(),  # pyright: ignore[reportPrivateUsage]
        tasks_raster._storage(),  # pyright: ignore[reportPrivateUsage]
        uuid.UUID(job_meta_id),
    )


def _preload() -> None:
    try:
        trocr.load_model()
    except trocr.ModelUnavailableError as exc:
        logger.warning("OCR model not loaded at worker start: %s", exc)


def preload_model_in_background() -> threading.Thread:
    """Load the model in a daemon thread at worker start, so the first job does not pay
    the cold load (about 6 s in the worker image); a job arriving first just waits for it
    (:func:`sketch2cad.ocr.trocr.load_model` is a locked singleton)."""
    thread = threading.Thread(target=_preload, name="ocr-preload", daemon=True)
    thread.start()
    return thread


def register(app: procrastinate.App) -> None:
    """Register :data:`OCR_TASK_NAME` on ``app``."""
    app.task(name=OCR_TASK_NAME)(ocr_crop)
