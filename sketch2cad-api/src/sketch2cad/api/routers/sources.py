"""Source endpoints: multipart ingestion into a case file (REQ-02, V-01..V-03)."""

import logging
import re
import uuid
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from sketch2cad.api.deps import (
    get_current_user,
    get_storage,
    require_case_permission,
    require_csrf,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.db.models import CaseFile, JobMeta, Source, User
from sketch2cad.db.session import get_session
from sketch2cad.sources.ingest import (
    ALTERED_MESSAGE,
    INVALID_TYPE_MESSAGE,
    MAX_UPLOAD_BYTES,
    TOO_LARGE_MESSAGE,
    AlteredUploadError,
    InvalidSourceTypeError,
    ingest_source,
)
from sketch2cad.sources.upload import (
    FILE_FIELD,
    MalformedUploadError,
    ReceivedUpload,
    UploadTooLargeError,
    read_upload,
)
from sketch2cad.storage import Storage

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


def _out(source: Source, job: JobMeta) -> SourceOut:
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
        qc_job_id=job.id,
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
    return _out(source, job)


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
