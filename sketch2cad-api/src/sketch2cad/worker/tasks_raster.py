"""Worker task ``rasterize``: the 300 dpi PNG of a single-page PDF source.

Entry points:

- In-process (what the ``qc`` task of 001-038 uses, since QC of a PDF needs its pixels):
  ``sketch2cad.sources.raster.rasterize_source(db, storage, source_id)`` inside the
  ``qc`` transaction, before reading the pixels from ``source.raster_key``. It is a no-op
  for images and for PDFs already rasterized.
- Queued: the Procrastinate task :data:`RASTERIZE_TASK_NAME` with the keyword argument
  ``source_id`` (UUID string), for example
  ``app.configure_task(RASTERIZE_TASK_NAME, connection=...).defer(source_id=...)``.
  It runs :func:`run_rasterize` in its own transaction.

Like the worker app, only ``DATABASE_URL`` and ``STORAGE_ROOT`` are read from the
environment; the API settings are not required.
"""

import logging
import os
import uuid
from functools import lru_cache

import procrastinate
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.config import Settings
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.sources.raster import rasterize_source
from sketch2cad.storage import FilesystemStorage, Storage

logger = logging.getLogger(__name__)

RASTERIZE_TASK_NAME = "rasterize"


class WorkerEnvironmentError(RuntimeError):
    """A variable the worker tasks need is missing from the environment."""


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise WorkerEnvironmentError("DATABASE_URL is not set")
    return create_session_factory(create_db_engine(database_url))


def _storage() -> Storage:
    default_root = str(Settings.model_fields["STORAGE_ROOT"].default)
    return FilesystemStorage(os.environ.get("STORAGE_ROOT") or default_root)


def run_rasterize(source_id: uuid.UUID) -> None:
    """Rasterize ``source_id`` and commit in one transaction (see ``rasterize_source``)."""
    with _session_factory()() as db, db.begin():
        rasterize_source(db, _storage(), source_id)


def rasterize(source_id: str) -> None:
    """Procrastinate task body: ``source_id`` is the UUID string of the source."""
    logger.info("Rasterize job started for source %s", source_id)
    run_rasterize(uuid.UUID(source_id))


def register(app: procrastinate.App) -> None:
    """Register :data:`RASTERIZE_TASK_NAME` on ``app``."""
    app.task(name=RASTERIZE_TASK_NAME)(rasterize)
