"""Worker task ``qc``: quality checks of a source, recorded in ``source.qc`` and ``job_meta``.

The API defers it on ingestion (``sketch2cad.sources.ingest.enqueue_qc``) with the keyword
arguments ``job_meta_id`` and ``source_id`` (UUID strings). The job's own ``source_id`` is
authoritative; the argument is only logged.

Like the rest of the worker, only ``DATABASE_URL`` and ``STORAGE_ROOT`` are required.
The thresholds resolve per job (:func:`~sketch2cad.jobs.service.run_qc_job`): override of
the incident type of the source's case, global override, then the optional ``QC_*``
variables (validated with the API settings constraints) or the spec values (§10), so the
worker never needs the API-only settings.
"""

import logging
import uuid

import procrastinate

from sketch2cad.jobs.service import QC_TASK_NAME, run_qc_job
from sketch2cad.qc import QcThresholds
from sketch2cad.worker import tasks_raster

logger = logging.getLogger(__name__)


def qc_thresholds_from_environment() -> QcThresholds:
    """QC thresholds from the ``QC_*`` environment variables (defaults when unset).

    Raises ``pydantic.ValidationError`` when a variable breaks the settings constraints.
    The job itself resolves them with the overrides of the source's incident type.
    """
    return QcThresholds.resolve(None, None)


def qc(job_meta_id: str, source_id: str) -> str:
    """Procrastinate task body; returns the final ``job_meta`` status."""
    logger.info("QC job %s started for source %s", job_meta_id, source_id)
    return run_qc_job(
        tasks_raster._session_factory(),  # pyright: ignore[reportPrivateUsage]
        tasks_raster._storage(),  # pyright: ignore[reportPrivateUsage]
        uuid.UUID(job_meta_id),
    )


def register(app: procrastinate.App) -> None:
    """Register :data:`QC_TASK_NAME` on ``app``."""
    app.task(name=QC_TASK_NAME)(qc)
