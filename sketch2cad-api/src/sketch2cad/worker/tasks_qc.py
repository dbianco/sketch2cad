"""Worker task ``qc``: quality checks of a source, recorded in ``source.qc`` and ``job_meta``.

The API defers it on ingestion (``sketch2cad.sources.ingest.enqueue_qc``) with the keyword
arguments ``job_meta_id`` and ``source_id`` (UUID strings). The job's own ``source_id`` is
authoritative; the argument is only logged.

Like the rest of the worker, only ``DATABASE_URL`` and ``STORAGE_ROOT`` are required.
The thresholds come from the optional ``QC_*`` variables, validated with the same
constraints as the API :class:`~sketch2cad.config.Settings`, and default to the spec
values (§10) when unset, so the worker never needs the API-only settings.
"""

import logging
import uuid
from typing import Any

import procrastinate
from pydantic import create_model
from pydantic_settings import BaseSettings, SettingsConfigDict

from sketch2cad.config import Settings
from sketch2cad.jobs.service import QC_TASK_NAME, run_qc_job
from sketch2cad.qc import QcThresholds
from sketch2cad.worker import tasks_raster

logger = logging.getLogger(__name__)

_THRESHOLD_SETTINGS = {
    "min_short_side_amber": "QC_MIN_SHORT_SIDE_AMBER",
    "min_short_side_red": "QC_MIN_SHORT_SIDE_RED",
    "min_laplacian_var_amber": "QC_MIN_LAPLACIAN_VAR_AMBER",
    "min_laplacian_var_red": "QC_MIN_LAPLACIAN_VAR_RED",
    "min_contrast_amber": "QC_MIN_CONTRAST_AMBER",
}


class _EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")


# Same type, default and constraints as the API settings field of each threshold.
_QC_FIELDS: dict[str, Any] = {
    name: (Settings.model_fields[name].annotation, Settings.model_fields[name])
    for name in _THRESHOLD_SETTINGS.values()
}
_QcEnvironment = create_model("_QcEnvironment", __base__=_EnvironmentSettings, **_QC_FIELDS)


def qc_thresholds_from_environment() -> QcThresholds:
    """QC thresholds from the ``QC_*`` environment variables (defaults when unset).

    Raises ``pydantic.ValidationError`` when a variable breaks the settings constraints.
    """
    values: dict[str, Any] = _QcEnvironment().model_dump()
    return QcThresholds(**{attr: values[name] for attr, name in _THRESHOLD_SETTINGS.items()})


def qc(job_meta_id: str, source_id: str) -> str:
    """Procrastinate task body; returns the final ``job_meta`` status."""
    logger.info("QC job %s started for source %s", job_meta_id, source_id)
    return run_qc_job(
        tasks_raster._session_factory(),  # pyright: ignore[reportPrivateUsage]
        tasks_raster._storage(),  # pyright: ignore[reportPrivateUsage]
        uuid.UUID(job_meta_id),
        qc_thresholds_from_environment(),
    )


def register(app: procrastinate.App) -> None:
    """Register :data:`QC_TASK_NAME` on ``app``."""
    app.task(name=QC_TASK_NAME)(qc)
