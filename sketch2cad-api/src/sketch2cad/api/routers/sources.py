"""Source endpoints: ingestion (REQ-02, V-01..V-03), metadata, original download and preview."""

import logging
import re
import uuid
from collections.abc import Iterator
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any, BinaryIO
from urllib.parse import quote

from fastapi import Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from sketch2cad.api.deps import (
    get_current_user,
    get_storage,
    require_case_permission,
    require_csrf,
    require_source_permission,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.db.models import CaseFile, JobMeta, Source, User
from sketch2cad.db.session import get_session
from sketch2cad.jobs.service import QC_JOB_KIND
from sketch2cad.sources.ingest import (
    ALTERED_MESSAGE,
    INVALID_TYPE_MESSAGE,
    MAX_UPLOAD_BYTES,
    TOO_LARGE_MESSAGE,
    AlteredUploadError,
    InvalidSourceTypeError,
    ingest_source,
)
from sketch2cad.sources.preview import (
    DEFAULT_PREVIEW_SIDE,
    MAX_PREVIEW_SIDE,
    MIN_PREVIEW_SIDE,
    PREVIEW_MEDIA_TYPE,
    PreviewError,
    preview_object,
    render_preview,
)
from sketch2cad.sources.upload import (
    FILE_FIELD,
    MalformedUploadError,
    ReceivedUpload,
    UploadTooLargeError,
    read_upload,
)
from sketch2cad.storage import ObjectNotFoundError, Storage
from sketch2cad.storage.base import CHUNK_SIZE

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["sources"])

CLIENT_SHA256_FIELD = "client_sha256"
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

UPLOAD_REQUEST_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": [FILE_FIELD, CLIENT_SHA256_FIELD],
                "properties": {
                    FILE_FIELD: {"type": "string", "format": "binary"},
                    CLIENT_SHA256_FIELD: {"type": "string", "pattern": _SHA256_PATTERN.pattern},
                },
            }
        }
    },
}

UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    413: {"description": "The file exceeds 50 MB (V-02)."},
    415: {"description": "Not a JPEG, PNG, TIFF or single-page PDF by content (V-01)."},
    422: {"description": "Malformed upload, or client_sha256 differs from the server (V-03)."},
}

CONTENT_SHA256_HEADER = "X-Content-SHA256"

ACCESS_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``read`` on the source's case file (access.denied)."},
    404: {"description": "The source does not exist."},
}

ORIGINAL_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The original bytes, unmodified, as an attachment.",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        "headers": {
            CONTENT_SHA256_HEADER: {
                "description": "SHA-256 registered at ingestion (lowercase hex).",
                "schema": {"type": "string"},
            }
        },
    },
    **ACCESS_ERRORS,
}

PREVIEW_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Derived JPEG whose longest side is at most ``max`` pixels.",
        "content": {PREVIEW_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
    },
    **ACCESS_ERRORS,
    422: {"description": "``max`` is outside 256..4096."},
}


class SourceOut(BaseModel):
    id: uuid.UUID
    case_file_id: uuid.UUID
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    width_px: int | None
    height_px: int | None
    dpi: float | None
    exif: dict[str, Any]
    qc: dict[str, Any] | None
    qc_job_id: uuid.UUID
    uploaded_by: uuid.UUID
    uploaded_at: datetime


def _out(source: Source, qc_job_id: uuid.UUID) -> SourceOut:
    return SourceOut(
        id=source.id,
        case_file_id=source.case_file_id,
        original_filename=source.original_filename,
        media_type=source.media_type,
        byte_size=source.byte_size,
        sha256=source.sha256,
        width_px=source.width_px,
        height_px=source.height_px,
        dpi=float(source.dpi) if source.dpi is not None else None,
        exif=source.exif,
        qc=source.qc,
        qc_job_id=qc_job_id,
        uploaded_by=source.uploaded_by,
        uploaded_at=source.uploaded_at,
    )


def _unprocessable(detail: str) -> ProblemError:
    return ProblemError(
        HTTPStatus.UNPROCESSABLE_ENTITY, HTTPStatus.UNPROCESSABLE_ENTITY.phrase, detail
    )


def _client_sha256(upload: ReceivedUpload) -> str:
    value = upload.fields.get(CLIENT_SHA256_FIELD, "").strip()
    if not _SHA256_PATTERN.fullmatch(value):
        raise _unprocessable("client_sha256 must be a 64-character hexadecimal SHA-256.")
    return value.lower()


def _ingest(
    db: Session,
    storage: Storage,
    case_file: CaseFile,
    user: User,
    upload: ReceivedUpload,
    client_ip: str | None,
) -> SourceOut:
    client_sha256 = _client_sha256(upload)
    try:
        source, job = ingest_source(
            db,
            storage,
            case_file=case_file,
            user=user,
            upload=upload,
            client_sha256=client_sha256,
            client_ip=client_ip,
        )
    except AlteredUploadError:
        db.rollback()
        logger.warning("Rejected upload to case file %s: hash mismatch", case_file.id)
        raise _unprocessable(ALTERED_MESSAGE) from None
    except InvalidSourceTypeError:
        db.rollback()
        logger.warning("Rejected upload to case file %s: invalid content type", case_file.id)
        raise ProblemError(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE.phrase,
            INVALID_TYPE_MESSAGE,
        ) from None
    db.commit()
    return _out(source, job.id)


@router.post(
    "/case-files/{case_id}/sources",
    status_code=201,
    dependencies=[Depends(require_csrf)],
    openapi_extra={"requestBody": UPLOAD_REQUEST_BODY},
    responses=UPLOAD_ERRORS,
)
async def upload_source(
    request: Request,
    case_file: Annotated[CaseFile, Depends(require_case_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> SourceOut:
    """Ingest one source file (multipart fields ``file`` and ``client_sha256``).

    The size cap is enforced while the body streams in (413). The server SHA-256 must equal
    ``client_sha256`` (422) and the content must be a JPEG, PNG, TIFF or single-page PDF by
    its bytes (415); nothing is stored when a rule fails. Requires ``edit``.
    """
    try:
        upload = await read_upload(
            request.headers.get("content-type"), request.stream(), max_file_bytes=MAX_UPLOAD_BYTES
        )
    except UploadTooLargeError:
        logger.warning("Rejected upload to case file %s: over the size cap", case_file.id)
        raise ProblemError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE.phrase,
            TOO_LARGE_MESSAGE,
        ) from None
    except MalformedUploadError:
        raise _unprocessable(
            "The upload must be multipart/form-data with one file part and client_sha256."
        ) from None
    try:
        client_ip = request.client.host if request.client else None
        return await run_in_threadpool(_ingest, db, storage, case_file, user, upload, client_ip)
    finally:
        upload.close()


def _qc_job_id(db: Session, source: Source) -> uuid.UUID:
    """Id of the latest ``qc`` job of ``source``; 500 if ingestion left none."""
    job_id = db.scalars(
        select(JobMeta.id)
        .where(JobMeta.source_id == source.id, JobMeta.kind == QC_JOB_KIND)
        .order_by(JobMeta.started_at.desc().nulls_first(), JobMeta.id)
        .limit(1)
    ).first()
    if job_id is None:
        logger.error("Source %s has no qc job", source.id)
        raise ProblemError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            HTTPStatus.INTERNAL_SERVER_ERROR.phrase,
            "The quality check of the source is not available.",
        )
    return job_id


@router.get("/sources/{source_id}", responses=ACCESS_ERRORS)
def get_source(
    source: Annotated[Source, Depends(require_source_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
) -> SourceOut:
    """Metadata of a source, including ``qc`` once its quality job has finished.

    ``qc`` is null while the job is queued or running. Requires ``read``; others get 403
    and an ``access.denied`` event.
    """
    return _out(source, _qc_job_id(db, source))


def _open_original(storage: Storage, source: Source, key: str | None = None) -> BinaryIO:
    """Open ``key`` (default: the original) of ``source``; 500 when the object is missing."""
    try:
        return storage.open(key if key is not None else source.storage_key)
    except ObjectNotFoundError:
        logger.error("Stored object of source %s is missing", source.id)
        raise ProblemError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            HTTPStatus.INTERNAL_SERVER_ERROR.phrase,
            "The stored original is not available.",
        ) from None


def _chunks(stream: BinaryIO) -> Iterator[bytes]:
    with stream:
        while chunk := stream.read(CHUNK_SIZE):
            yield chunk


def _attachment(filename: str) -> str:
    """``Content-Disposition`` value with an ASCII fallback and the RFC 5987 UTF-8 name."""
    fallback = "".join(
        c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in filename
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get(
    "/sources/{source_id}/original", response_class=StreamingResponse, responses=ORIGINAL_RESPONSES
)
def download_original(
    request: Request,
    source: Annotated[Source, Depends(require_source_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> StreamingResponse:
    """Stream the original bytes exactly as stored, with ``X-Content-SHA256``.

    Writes ``source.downloaded`` before the first byte is sent. Requires ``read``; others
    get 403 and an ``access.denied`` event.
    """
    stream = _open_original(storage, source)
    try:
        append_event(
            db,
            case_file_id=source.case_file_id,
            kind=EventKind.SOURCE_DOWNLOADED,
            actor_id=user.id,
            actor_role=user.role.value,
            entity_id=source.id,
            after={"sha256": source.sha256, "byte_size": source.byte_size},
            client_ip=request.client.host if request.client else None,
        )
        db.commit()
    except Exception:
        stream.close()
        raise
    logger.info("User %s downloaded source %s", user.id, source.id)
    headers = {
        CONTENT_SHA256_HEADER: source.sha256,
        "Content-Length": str(source.byte_size),
        "Content-Disposition": _attachment(source.original_filename),
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(_chunks(stream), media_type=source.media_type, headers=headers)


@router.get("/sources/{source_id}/preview", response_class=Response, responses=PREVIEW_RESPONSES)
def preview_source(
    source: Annotated[Source, Depends(require_source_permission("read"))],
    storage: Annotated[Storage, Depends(get_storage)],
    max_side: Annotated[
        int, Query(alias="max", ge=MIN_PREVIEW_SIDE, le=MAX_PREVIEW_SIDE)
    ] = DEFAULT_PREVIEW_SIDE,
) -> Response:
    """Derived JPEG of the source whose longest side is at most ``max`` (256 to 4096).

    PDFs are rendered from page 1; no EXIF is carried over. Requires ``read``.
    """
    # PDFs use their stored 300 dpi raster once the worker made it (see preview_object).
    key, media_type = preview_object(source)
    with _open_original(storage, source, key) as stream:
        try:
            content = render_preview(stream, media_type, max_side)
        except PreviewError:
            logger.error("Could not render a preview of source %s", source.id)
            raise ProblemError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                HTTPStatus.INTERNAL_SERVER_ERROR.phrase,
                "The preview could not be generated.",
            ) from None
    return Response(content=content, media_type=PREVIEW_MEDIA_TYPE)
