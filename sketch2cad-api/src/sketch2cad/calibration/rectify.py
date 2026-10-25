"""Rectified image of an accepted calibration: the ``rectify`` job (spec sections 9.2, 10, 11).

The original is warped with ``cv2.warpPerspective`` into a canvas of paper millimetres at
:data:`PX_PER_MM` px/mm and stored as a write-once PNG artifact of kind
``rectified_image`` (``plan_id`` set, ``revision_id`` null). The canvas is the rectangle
of known dimensions for ``photo_homography_4pt`` (the bounding box of the control points'
paper positions, from the paper origin), and the whole image mapped through the pure
scale matrix for ``scan_dpi`` and ``scan_segment``. ``none`` has no rectified image.

Idempotency is keyed on the calibration content (:func:`rectify_key`: source, method,
matrix and paper positions). Accepting the same calibration again replaces the
calibration row but enqueues no second job while one with the same key is queued,
running or succeeded; a different calibration, such as a recalibration of an empty plan,
gets its own job. ``job_meta.params`` holds ``calibration_id``, ``source_id`` and
``rectify_key``; ``job_meta.result_ref`` the id of the artifact. Identical pixels reuse
the existing artifact row. Logs carry identifiers only.
"""

import hashlib
import io
import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import sketch2cad.worker.app as worker_app
from sketch2cad.db.models import (
    Artifact,
    ArtifactKind,
    Calibration,
    CalibrationMethod,
    JobMeta,
    JobStatus,
    MediaType,
    Plan,
    Source,
)
from sketch2cad.geometry import TransformError, apply_h, homography_from_json
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
from sketch2cad.sources.raster import RasterError, rasterize_source
from sketch2cad.storage import ObjectNotFoundError, Storage

logger = logging.getLogger(__name__)

RECTIFY_JOB_KIND = "rectify"
RECTIFY_TASK_NAME = "rectify"
"""Procrastinate task name of the rectify job (``sketch2cad.worker.tasks_rectify``)."""

PX_PER_MM = 10
"""Resolution of the rectified canvas (spec section 10)."""

GENERATOR_VERSION = f"sketch2cad-rectify/1 opencv/{cv2.__version__}"
_MM_PER_INCH = 25.4
_ACTIVE_JOB_STATUSES = (JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.SUCCEEDED.value)

MISSING_CALIBRATION = JobError(
    "La calibración del trabajo ya no existe.",
    "Vuelve a aceptar la calibración del plano.",
)
CANVAS_TOO_LARGE = JobError(
    "La imagen rectificada sería demasiado grande para generarse.",
    "Revisa las dimensiones conocidas o los puntos de calibración y vuelve a aceptarla.",
)
INTERNAL_ERROR = JobError(
    "Error interno al generar la imagen rectificada.",
    "Vuelve a intentarlo más tarde; si el problema continúa, contacta al administrador.",
)


class RectifyError(ValueError):
    """The calibration defines no canvas or the canvas exceeds the pixel budget."""


class RectifyJobError(Exception):
    """The ``rectify`` job cannot produce its image; carries the user-facing error."""

    def __init__(self, error: JobError, reason: str) -> None:
        super().__init__(reason)
        self.error = error


def paper_size_mm(
    method: str,
    image_to_paper: Sequence[Sequence[float]],
    control_points: Sequence[dict[str, Any]],
    image_size_px: tuple[int, int],
) -> tuple[float, float]:
    """Width and height in paper mm of the rectified canvas of a calibration.

    ``image_size_px`` is ``(width, height)`` of the original. For a homography the canvas
    spans from the paper origin to the farthest control point (the known rectangle); for
    the scan methods it is the whole image mapped through the scale matrix.
    """
    if method == CalibrationMethod.PHOTO_HOMOGRAPHY_4PT.value:
        paper = [p["paper_mm"] for p in control_points if p.get("paper_mm") is not None]
        corners = [(float(x), float(y)) for x, y in paper]
    elif method in (CalibrationMethod.SCAN_DPI.value, CalibrationMethod.SCAN_SEGMENT.value):
        width, height = float(image_size_px[0]), float(image_size_px[1])
        try:
            matrix = homography_from_json(image_to_paper)
        except TransformError as exc:
            raise RectifyError(str(exc)) from exc
        image_corners = ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
        corners = [apply_h(matrix, c) for c in image_corners]
    else:
        raise RectifyError(f"method {method} has no rectified image")
    size = (max((x for x, _ in corners), default=0.0), max((y for _, y in corners), default=0.0))
    if not all(np.isfinite(size)) or min(size) <= 0:
        raise RectifyError("calibration defines an empty canvas")
    return size


def rectify_image(
    pixels: npt.NDArray[Any],
    image_to_paper: Sequence[Sequence[float]] | npt.NDArray[np.float64],
    paper_size_mm: tuple[float, float],
    px_per_mm: int = PX_PER_MM,
) -> npt.NDArray[Any]:
    """Warp ``pixels`` into a ``paper_size_mm`` canvas at ``px_per_mm`` (white outside).

    ``image_to_paper`` maps image px to paper mm. The canvas has
    ``round(width_mm * px_per_mm)`` x ``round(height_mm * px_per_mm)`` pixels; raises
    :class:`RectifyError` when it is empty or exceeds Pillow's ``MAX_IMAGE_PIXELS``.
    """
    width = round(paper_size_mm[0] * px_per_mm)
    height = round(paper_size_mm[1] * px_per_mm)
    if width <= 0 or height <= 0:
        raise RectifyError("rectified canvas is empty")
    if width * height > (Image.MAX_IMAGE_PIXELS or float("inf")):
        raise RectifyError(f"rectified canvas of {width}x{height} px is too large")
    to_canvas = np.diag([float(px_per_mm), float(px_per_mm), 1.0]) @ np.asarray(
        image_to_paper, dtype=np.float64
    )
    white = float(np.iinfo(pixels.dtype).max) if pixels.dtype.kind in "ui" else 1.0
    return cv2.warpPerspective(
        pixels,
        to_canvas,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(white, white, white, white),
    )


def rectify_key(calibration: Calibration, source_id: uuid.UUID) -> str:
    """SHA-256 of what determines the rectified image of ``calibration``."""
    paper = [p.get("paper_mm") for p in calibration.control_points if p.get("role") == "control"]
    content = {
        "source_id": str(source_id),
        "method": calibration.method,
        "image_to_paper": calibration.image_to_paper,
        "paper_mm": paper,
        "px_per_mm": PX_PER_MM,
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def enqueue_rectify(db: Session, plan: Plan, calibration: Calibration) -> JobMeta | None:
    """Queue the ``rectify`` job of ``calibration`` in the caller's transaction.

    Returns ``None`` for ``none``, the existing job when one with the same
    :func:`rectify_key` is queued, running or succeeded, and otherwise a new ``queued``
    ``job_meta`` row whose Procrastinate task is deferred on the session's connection.
    """
    if calibration.method == CalibrationMethod.NONE.value:
        return None
    key = rectify_key(calibration, plan.primary_source_id)
    existing = db.scalars(
        select(JobMeta)
        .where(
            JobMeta.plan_id == plan.id,
            JobMeta.kind == RECTIFY_JOB_KIND,
            JobMeta.params["rectify_key"].astext == key,
            JobMeta.status.in_(_ACTIVE_JOB_STATUSES),
        )
        .limit(1)
    ).first()
    if existing is not None:
        logger.info("Rectify job %s already covers calibration %s", existing.id, calibration.id)
        return existing
    job = JobMeta(
        id=uuid.uuid4(),
        plan_id=plan.id,
        source_id=plan.primary_source_id,
        kind=RECTIFY_JOB_KIND,
        params={
            "calibration_id": str(calibration.id),
            "source_id": str(plan.primary_source_id),
            "rectify_key": key,
        },
        status=JobStatus.QUEUED.value,
    )
    db.add(job)
    db.flush()
    connection = db.connection().connection.driver_connection
    worker_app.app.configure_task(RECTIFY_TASK_NAME, connection=connection).defer(
        job_meta_id=str(job.id)
    )
    logger.info("Queued rectify job %s for calibration %s", job.id, calibration.id)
    return job


def _decode(data: bytes) -> npt.NDArray[Any]:
    """Colour stays RGB, 16-bit gray keeps its depth, every other mode becomes 8-bit gray."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode.startswith("I;16"):
                return np.asarray(image.convert("I;16"))
            gray = image.mode in {"1", "L", "LA", "I", "F"}
            return np.asarray(image.convert("L" if gray else "RGB"))
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise RectifyJobError(UNREADABLE_IMAGE, "image does not decode") from exc


def _source_pixels(db: Session, storage: Storage, source: Source) -> npt.NDArray[Any]:
    """Pixels the calibration was marked on: the 300 dpi raster for PDFs."""
    try:
        if source.media_type == MediaType.PDF.value:
            source = rasterize_source(db, storage, source.id)
        data = storage.get(source.raster_key or source.storage_key)
    except RasterError as exc:
        raise RectifyJobError(UNREADABLE_PDF, "PDF does not rasterize") from exc
    except ObjectNotFoundError as exc:
        raise RectifyJobError(MISSING_OBJECT, "stored object is missing") from exc
    return _decode(data)


def _encode_png(pixels: npt.NDArray[Any]) -> bytes:
    buffer = io.BytesIO()
    dpi = PX_PER_MM * _MM_PER_INCH
    Image.fromarray(pixels).save(buffer, format="PNG", dpi=(dpi, dpi))
    return buffer.getvalue()


def _store_artifact(db: Session, storage: Storage, plan_id: uuid.UUID, png: bytes) -> Artifact:
    stored = storage.put(io.BytesIO(png))
    artifact = db.scalars(
        select(Artifact).where(
            Artifact.plan_id == plan_id,
            Artifact.revision_id.is_(None),
            Artifact.kind == ArtifactKind.RECTIFIED_IMAGE.value,
            Artifact.sha256 == stored.sha256,
        )
    ).first()
    if artifact is None:
        artifact = Artifact(
            plan_id=plan_id,
            revision_id=None,
            kind=ArtifactKind.RECTIFIED_IMAGE.value,
            is_draft=False,
            sha256=stored.sha256,
            storage_key=stored.key,
            byte_size=len(png),
            generator_version=GENERATOR_VERSION,
        )
        db.add(artifact)
        db.flush()
    return artifact


def _rectify(db: Session, storage: Storage, job: JobMeta) -> Artifact:
    calibration_id = job.params.get("calibration_id")
    calibration = db.get(Calibration, uuid.UUID(calibration_id)) if calibration_id else None
    if calibration is None or calibration.image_to_paper is None:
        raise RectifyJobError(MISSING_CALIBRATION, "job has no calibration with a matrix")
    source = db.get(Source, job.source_id) if job.source_id is not None else None
    if source is None:
        raise RectifyJobError(MISSING_SOURCE, "job has no existing source")
    with db.begin_nested():
        pixels = _source_pixels(db, storage, source)
    height, width = pixels.shape[:2]
    try:
        size = paper_size_mm(
            calibration.method,
            calibration.image_to_paper,
            calibration.control_points,
            (width, height),
        )
        rectified = rectify_image(pixels, calibration.image_to_paper, size)
    except RectifyError as exc:
        raise RectifyJobError(CANVAS_TOO_LARGE, str(exc)) from exc
    return _store_artifact(db, storage, calibration.plan_id, _encode_png(rectified))


def _record_internal_failure(factory: sessionmaker[Session], job_id: uuid.UUID) -> None:
    with factory() as db, db.begin():
        job = _locked_job(db, job_id)
        if job.status not in TERMINAL_STATUSES:
            _finish(job, JobStatus.FAILED, INTERNAL_ERROR)


def run_rectify_job(factory: sessionmaker[Session], storage: Storage, job_id: uuid.UUID) -> str:
    """Run the ``rectify`` job ``job_id`` and return its final status.

    Same lifecycle as the ``qc`` job: ``running`` commits first, then the artifact and the
    final status commit together; a finished job is left untouched. Expected failures end
    ``failed`` with a Spanish ``{cause, action}``; an unexpected error ends ``failed``
    with :data:`INTERNAL_ERROR` and is re-raised.
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
                artifact = _rectify(db, storage, job)
            except RectifyJobError as exc:
                _finish(job, JobStatus.FAILED, exc.error)
                logger.warning("Rectify job %s failed: %s", job.id, exc)
                return job.status
            job.result_ref = str(artifact.id)
            _finish(job, JobStatus.SUCCEEDED)
            logger.info("Rectify job %s stored artifact %s", job.id, artifact.id)
            return job.status
    except Exception:
        logger.exception("Rectify job %s failed unexpectedly", job_id)
        _record_internal_failure(factory, job_id)
        raise
