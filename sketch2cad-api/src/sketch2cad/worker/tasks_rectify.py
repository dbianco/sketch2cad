"""Worker task ``rectify``: the rectified image of an accepted calibration.

The API defers it on calibration acceptance (``sketch2cad.calibration.rectify.
enqueue_rectify``) with the keyword argument ``job_meta_id`` (UUID string); the job's
``params`` say which calibration and source to use. It runs
:func:`~sketch2cad.calibration.rectify.run_rectify_job`.

Like the rest of the worker, only ``DATABASE_URL`` and ``STORAGE_ROOT`` are required.
"""

import logging
import uuid

import procrastinate

import sketch2cad.calibration.rectify as rectify_job
from sketch2cad.worker import tasks_raster

logger = logging.getLogger(__name__)


def rectify(job_meta_id: str) -> str:
    """Procrastinate task body; returns the final ``job_meta`` status."""
    logger.info("Rectify job %s started", job_meta_id)
    return rectify_job.run_rectify_job(
        tasks_raster._session_factory(),  # pyright: ignore[reportPrivateUsage]
        tasks_raster._storage(),  # pyright: ignore[reportPrivateUsage]
        uuid.UUID(job_meta_id),
    )


def register(app: procrastinate.App) -> None:
    """Register :data:`~sketch2cad.calibration.rectify.RECTIFY_TASK_NAME` on ``app``."""
    app.task(name=rectify_job.RECTIFY_TASK_NAME)(rectify)
