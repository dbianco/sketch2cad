"""Source ingestion: validation rules V-01..V-03, storage, metadata and the ``qc`` job.

Logs carry identifiers only: never file names, bytes or EXIF content (H1-9).
"""

import logging
import math
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import IO, Any

import pypdfium2  # pyright: ignore[reportMissingTypeStubs]
from PIL import ExifTags, Image, UnidentifiedImageError
from sqlalchemy.orm import Session

import sketch2cad.worker.app as worker_app
from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.auth.lockout import utc_now
from sketch2cad.db.models import CaseFile, JobMeta, JobStatus, MediaType, Source, User
from sketch2cad.jobs.service import QC_JOB_KIND, QC_TASK_NAME
from sketch2cad.sources.magic import SNIFF_BYTES, sniff_media_type
from sketch2cad.sources.upload import ReceivedUpload
from sketch2cad.storage import Storage

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
"""V-02: maximum size of an uploaded file."""

INVALID_TYPE_MESSAGE = "El archivo no es un JPEG, PNG, TIFF o PDF de una página válido."
TOO_LARGE_MESSAGE = "El archivo supera los 50 MB permitidos."
ALTERED_MESSAGE = "El archivo se alteró durante la subida. Vuelve a cargarlo."
# ``cause`` and ``action`` members of the rejected-upload problems (REQ-15).
INVALID_TYPE_ACTION = (
    "Vuelve a cargar el archivo original del croquis, o expórtalo como JPEG, PNG, TIFF o "
    "PDF de una sola página."
)
TOO_LARGE_ACTION = "Reduce la resolución de la foto o del escaneo y vuelve a cargarlo."
ALTERED_CAUSE = "El archivo se alteró durante la subida."
ALTERED_ACTION = "Vuelve a cargarlo."

_PILLOW_FORMATS: dict[MediaType, frozenset[str]] = {
    MediaType.JPEG: frozenset({"JPEG", "MPO"}),
    MediaType.PNG: frozenset({"PNG"}),
    MediaType.TIFF: frozenset({"TIFF"}),
}
_EXIF_SUB_IFDS = (ExifTags.IFD.Exif, ExifTags.IFD.GPSInfo)
# TIFF tags that locate image data; they can hold thousands of offsets and say nothing.
_SKIPPED_TAGS = frozenset({"StripOffsets", "StripByteCounts", "TileOffsets", "TileByteCounts"})


class InvalidSourceTypeError(ValueError):
    """V-01: the content is not a readable JPEG, PNG, TIFF or single-page PDF."""


class AlteredUploadError(ValueError):
    """V-03: the server-side SHA-256 differs from ``client_sha256``."""


@dataclass(frozen=True)
class SourceMetadata:
    media_type: MediaType
    width_px: int | None
    height_px: int | None
    dpi: float | None
    exif: dict[str, Any]


def _json_value(value: Any) -> Any:
    """``value`` as JSON-safe data for JSONB, or ``None`` when it should be skipped."""
    if isinstance(value, bool | int | str):
        return value.replace("\x00", "") if isinstance(value, str) else value
    if isinstance(value, tuple | list):
        items = [_json_value(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
        return None if any(item is None for item in items) else items
    try:
        number = float(value)  # IFDRational and other numeric types
    except (TypeError, ValueError, ZeroDivisionError):
        return None  # bytes (MakerNote, UserComment) and unknown types are not kept
    return number if math.isfinite(number) else None


def _exif_dict(image: Image.Image) -> dict[str, Any]:
    """Base, Exif and GPS IFD tags by name; GPS tag numbers overlap the others, so each
    IFD is named with its own table."""
    exif = image.getexif()
    ifds: list[tuple[dict[int, str], dict[int, Any]]] = [
        (ExifTags.TAGS, dict(exif)),
        (ExifTags.TAGS, dict(exif.get_ifd(ExifTags.IFD.Exif))),
        (ExifTags.GPSTAGS, dict(exif.get_ifd(ExifTags.IFD.GPSInfo))),
    ]
    result: dict[str, Any] = {}
    for names, tags in ifds:
        for tag, raw in tags.items():
            name = names.get(tag, str(tag))
            value = None if name in _SKIPPED_TAGS or tag in _EXIF_SUB_IFDS else _json_value(raw)
            if value is not None:
                result[name] = value
    return result


def _dpi(image: Image.Image) -> float | None:
    """Horizontal DPI when the file declares a positive, isotropic density."""
    raw: Any = image.info.get("dpi")
    if not isinstance(raw, tuple) or len(raw) != 2:  # pyright: ignore[reportUnknownArgumentType]
        return None
    try:
        x, y = float(raw[0]), float(raw[1])  # pyright: ignore[reportUnknownArgumentType]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not (math.isfinite(x) and x > 0 and math.isclose(x, y, abs_tol=0.01)):
        return None
    return round(x, 2)


def _inspect_pdf(stream: IO[bytes]) -> SourceMetadata:
    try:
        document = pypdfium2.PdfDocument(stream)
    except pypdfium2.PdfiumError:
        raise InvalidSourceTypeError("unreadable PDF") from None
    try:
        if len(document) != 1:
            raise InvalidSourceTypeError("PDF does not have exactly one page")
    finally:
        document.close()
    # Size and resolution come from the 300 dpi raster made in the worker (task 001-036).
    return SourceMetadata(MediaType.PDF, None, None, None, {})


def inspect_source(stream: IO[bytes]) -> SourceMetadata:
    """Validate the content of ``stream`` (V-01) and read size, DPI and EXIF."""
    stream.seek(0)
    media_type = sniff_media_type(stream.read(SNIFF_BYTES))
    stream.seek(0)
    if media_type is None:
        raise InvalidSourceTypeError("unrecognised signature")
    if media_type is MediaType.PDF:
        return _inspect_pdf(stream)
    try:
        with Image.open(stream) as image:
            if image.format not in _PILLOW_FORMATS[media_type]:
                raise InvalidSourceTypeError("image format does not match its signature")
            width, height = image.size
            return SourceMetadata(media_type, width, height, _dpi(image), _exif_dict(image))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise InvalidSourceTypeError("unreadable image") from None


def enqueue_qc(session: Session, source: Source) -> JobMeta:
    """Queue the ``qc`` job of ``source`` in the caller's transaction.

    Inserts a ``queued`` ``job_meta`` row and defers the Procrastinate task
    :data:`QC_TASK_NAME` with ``job_meta_id`` and ``source_id`` on the session's own
    connection, so both rows commit or roll back together.
    """
    job = JobMeta(
        id=uuid.uuid4(),
        source_id=source.id,
        kind=QC_JOB_KIND,
        params={"source_id": str(source.id)},
        status=JobStatus.QUEUED.value,
    )
    session.add(job)
    session.flush()
    connection = session.connection().connection.driver_connection
    worker_app.app.configure_task(QC_TASK_NAME, connection=connection).defer(
        job_meta_id=str(job.id), source_id=str(source.id)
    )
    logger.info("Queued qc job %s for source %s", job.id, source.id)
    return job


def ingest_source(
    db: Session,
    storage: Storage,
    *,
    case_file: CaseFile,
    user: User,
    upload: ReceivedUpload,
    client_sha256: str,
    client_ip: str | None,
) -> tuple[Source, JobMeta]:
    """Check V-03 then V-01, store the original and record ``source.ingested`` and ``qc``.

    Nothing is stored when a rule fails. The caller commits.
    """
    if upload.sha256 != client_sha256:
        raise AlteredUploadError("server and client SHA-256 differ")
    metadata = inspect_source(upload.file)
    upload.file.seek(0)
    stored = storage.put(upload.file)
    if stored.sha256 != upload.sha256:
        raise RuntimeError("stored object hash differs from the received bytes")
    source = Source(
        id=uuid.uuid4(),
        case_file_id=case_file.id,
        original_filename=upload.filename,
        media_type=metadata.media_type.value,
        byte_size=upload.byte_size,
        sha256=stored.sha256,
        storage_key=stored.key,
        width_px=metadata.width_px,
        height_px=metadata.height_px,
        dpi=Decimal(str(metadata.dpi)) if metadata.dpi is not None else None,
        exif=metadata.exif,
        uploaded_by=user.id,
        uploaded_at=utc_now(),
    )
    db.add(source)
    db.flush()
    append_event(
        db,
        case_file_id=case_file.id,
        kind=EventKind.SOURCE_INGESTED,
        actor_id=user.id,
        actor_role=user.role.value,
        entity_id=source.id,
        after={
            "original_filename": source.original_filename,
            "media_type": source.media_type,
            "byte_size": source.byte_size,
            "sha256": source.sha256,
            "width_px": source.width_px,
            "height_px": source.height_px,
            "dpi": metadata.dpi,
        },
        client_ip=client_ip,
    )
    job = enqueue_qc(db, source)
    logger.info("Ingested source %s into case file %s", source.id, case_file.id)
    return source, job
