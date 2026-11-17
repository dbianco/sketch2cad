"""``job_meta`` lifecycle and the quality-control job of a source.

A job moves ``queued`` -> ``running`` -> ``succeeded`` or ``failed``. A failed job carries
``error = {"cause", "action"}`` in Spanish, shown to the technician as is (REQ-15). The
``qc`` job of an unreadable source fails and stores a red ``source.qc``: it never leaves a
default green. Logs carry identifiers only.
"""

import io
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.auth.lockout import utc_now
from sketch2cad.config_params import case_incident_type
from sketch2cad.db.models import JobMeta, JobStatus, MediaType, Source
from sketch2cad.qc import QcThresholds, evaluate
from sketch2cad.sources.raster import RasterError, rasterize_source
from sketch2cad.storage import ObjectNotFoundError, Storage

logger = logging.getLogger(__name__)

QC_JOB_KIND = "qc"
QC_TASK_NAME = "qc"
"""Procrastinate task name of the quality-control job (``sketch2cad.worker.tasks_qc``)."""

TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED.value, JobStatus.FAILED.value})


@dataclass(frozen=True)
class JobError:
    """Why a job failed and what the user can do about it, both in Spanish (REQ-15)."""

    cause: str
    action: str

    def to_dict(self) -> dict[str, str]:
        """JSON form stored in ``job_meta.error``."""
        return {"cause": self.cause, "action": self.action}


UNREADABLE_IMAGE = JobError(
    "No se pudo leer la imagen: el archivo está dañado o incompleto.",
    "Vuelve a cargar el archivo original o una nueva foto o escaneo del croquis.",
)
UNREADABLE_PDF = JobError(
    "No se pudo convertir el PDF en imagen.",
    "Vuelve a cargar el PDF o exporta la página como imagen JPEG o PNG.",
)
MISSING_OBJECT = JobError(
    "El archivo de la fuente no está disponible en el almacenamiento.",
    "Contacta al administrador del sistema.",
)
MISSING_SOURCE = JobError(
    "La fuente del trabajo no existe.",
    "Vuelve a cargar el archivo en el expediente.",
)
INTERNAL_ERROR = JobError(
    "Error interno al analizar la calidad de la imagen.",
    "Vuelve a intentarlo más tarde; si el problema continúa, contacta al administrador.",
)


class JobNotFoundError(LookupError):
    """No ``job_meta`` row exists with the requested id."""


class QcJobError(Exception):
    """The ``qc`` job cannot analyze its source; carries the user-facing error."""

    def __init__(self, error: JobError, reason: str) -> None:
        super().__init__(reason)
        self.error = error


def _locked_job(db: Session, job_id: uuid.UUID) -> JobMeta:
    job = db.scalars(select(JobMeta).where(JobMeta.id == job_id).with_for_update()).first()
    if job is None:
        raise JobNotFoundError(f"job {job_id} does not exist")
    return job


def _finish(job: JobMeta, status: JobStatus, error: JobError | None = None) -> None:
    job.status = status.value
    job.error = error.to_dict() if error is not None else None
    job.finished_at = utc_now()


def decode_image(data: bytes) -> npt.NDArray[Any]:
    """Fully decode ``data`` into a numpy array that :func:`sketch2cad.qc.evaluate` accepts.

    16-bit and 32-bit grayscale keep their depth; every other mode becomes 8-bit gray.
    Raises :class:`QcJobError` with :data:`UNREADABLE_IMAGE` when the bytes do not decode
    completely (truncated or corrupt data, decompression bombs).
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode.startswith("I;16") or image.mode in {"I", "F"}:
                return np.asarray(image)
            return np.asarray(image.convert("L"))
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise QcJobError(UNREADABLE_IMAGE, f"image does not decode: {type(exc).__name__}") from exc


def _source_pixels(db: Session, storage: Storage, source: Source) -> npt.NDArray[Any]:
    """Pixels of ``source``: its stored raster for PDFs (made here when missing)."""
    try:
        if source.media_type == MediaType.PDF.value:
            source = rasterize_source(db, storage, source.id)
        data = storage.get(source.raster_key or source.storage_key)
    except RasterError as exc:
        raise QcJobError(UNREADABLE_PDF, "PDF does not rasterize") from exc
    except ObjectNotFoundError as exc:
        raise QcJobError(MISSING_OBJECT, "stored object is missing") from exc
    return decode_image(data)


def _evaluate_job(
    db: Session, storage: Storage, job: JobMeta, given: QcThresholds | None
) -> dict[str, Any]:
    """Run the checks of the job's source and store ``source.qc``; return its status.

    Without ``given`` thresholds they resolve for the incident type of the source's case.
    """
    source = db.get(Source, job.source_id) if job.source_id is not None else None
    if source is None:
        raise QcJobError(MISSING_SOURCE, "job has no existing source")
    thresholds = given or QcThresholds.resolve(db, case_incident_type(db, source.case_file_id))
    try:
        with db.begin_nested():
            pixels = _source_pixels(db, storage, source)
    except QcJobError:
        # Never a default green: an unanalyzable source gets the red ``input`` check.
        source.qc = evaluate(None, thresholds).to_dict()
        raise
    qc = evaluate(pixels, thresholds).to_dict()
    source.qc = qc
    return qc


def _start(factory: sessionmaker[Session], job_id: uuid.UUID) -> bool:
    """Mark the job ``running`` and commit; ``False`` when it already finished."""
    with factory() as db, db.begin():
        job = _locked_job(db, job_id)
        if job.status in TERMINAL_STATUSES:
            logger.info("Job %s already %s; nothing to do", job.id, job.status)
            return False
        job.status = JobStatus.RUNNING.value
        job.started_at = utc_now()
        job.error = None
    return True


def _record_internal_failure(factory: sessionmaker[Session], job_id: uuid.UUID) -> None:
    with factory() as db, db.begin():
        job = _locked_job(db, job_id)
        if job.status not in TERMINAL_STATUSES:
            _finish(job, JobStatus.FAILED, INTERNAL_ERROR)


def run_qc_job(
    factory: sessionmaker[Session],
    storage: Storage,
    job_id: uuid.UUID,
    thresholds: QcThresholds | None = None,
) -> str:
    """Run the ``qc`` job ``job_id`` and return its final status.

    The ``running`` state is committed first so pollers see it. The checks, ``source.qc``
    and the final status then commit together in a second transaction. Retries are
    idempotent: a finished job is left untouched, and a job interrupted while running is
    simply analyzed again (rasterizing and evaluating the same bytes give the same
    result). An unexpected error marks the job ``failed`` with :data:`INTERNAL_ERROR`
    and is re-raised so the queue records it too. Raises :class:`JobNotFoundError` when
    the job does not exist. ``thresholds`` default to the configuration of the incident
    type of the source's case file (:meth:`QcThresholds.resolve`).
    """
    if not _start(factory, job_id):
        with factory() as db:
            job = db.get(JobMeta, job_id)
            return job.status if job is not None else JobStatus.FAILED.value
    try:
        with factory() as db, db.begin():
            job = _locked_job(db, job_id)
            if job.status in TERMINAL_STATUSES:  # a concurrent run finished it
                return job.status
            try:
                qc = _evaluate_job(db, storage, job, thresholds)
            except QcJobError as exc:
                _finish(job, JobStatus.FAILED, exc.error)
                logger.warning("QC job %s failed: %s", job.id, exc)
                return job.status
            _finish(job, JobStatus.SUCCEEDED)
            logger.info("QC job %s succeeded with status %s", job.id, qc["status"])
            return job.status
    except Exception:
        logger.exception("QC job %s failed unexpectedly", job_id)
        _record_internal_failure(factory, job_id)
        raise
