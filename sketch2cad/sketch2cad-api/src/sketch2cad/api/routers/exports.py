"""Export requests and artifact download (spec section 9.1; REQ-12..REQ-14, US-15, US-16, V-11).

``POST /plans/{id}/exports`` queues an ``export`` job (202, polled at ``GET /jobs/{id}``).
``GET /artifacts/{id}`` shows the artifact, its hash and, for final artifacts, the signed
manifest of its package; only ``?confirm=true`` streams the bytes and writes
``artifact.downloaded`` (exactly one event per confirmed download, before the first byte).
"""

import json
import logging
import uuid
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api.deps import (
    _deny,  # pyright: ignore[reportPrivateUsage]
    get_audit_session_factory,
    get_current_user,
    get_storage,
    require_csrf,
    require_plan_permission,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.api.routers.jobs import JobErrorOut, JobOut
from sketch2cad.api.routers.sources import (
    CONTENT_SHA256_HEADER,
    _attachment,  # pyright: ignore[reportPrivateUsage]
    _chunks,  # pyright: ignore[reportPrivateUsage]
)
from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.cases.permissions import CasePermission, case_permissions
from sketch2cad.db.models import Artifact, ArtifactKind, CaseFile, Plan, Revision, User
from sketch2cad.db.session import get_session
from sketch2cad.exports.service import (
    ExportConflictError,
    ExportMode,
    ExportScale,
    PaperChoice,
    artifact_filename,
    artifact_media_type,
    is_final_artifact,
    request_export,
)
from sketch2cad.storage import ObjectNotFoundError, Storage

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["exports"])

EXPORT_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "The user lacks ``edit`` (draft) or ``deliver`` (final) on the plan's "
        "case file (access.denied)."
    },
    404: {"description": "The plan does not exist."},
    409: {
        "description": "``final`` on a plan that is not ``VALIDATED`` (``code`` ``V-11``), or "
        "``draft`` before ``EDITING`` (``code`` ``draft_not_allowed``)."
    },
}
ARTIFACT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Without ``confirm``: the artifact metadata (JSON). With "
        "``confirm=true``: the bytes as an attachment with ``X-Content-SHA256``.",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    },
    403: {
        "description": "The user lacks ``read``, or ``deliver`` to download a final artifact "
        "(access.denied)."
    },
    404: {"description": "The artifact does not exist."},
}

LIST_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``read`` on the plan's case file (access.denied)."},
    404: {"description": "The plan does not exist."},
}


class ExportIn(BaseModel):
    """``scale`` null uses the proposed scale; ``paper`` ``auto`` the proposed sheet."""

    model_config = ConfigDict(extra="forbid")

    mode: ExportMode
    paper: PaperChoice = "auto"
    scale: ExportScale | None = None


class ArtifactOut(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    revision_id: uuid.UUID | None
    kind: Literal["dxf", "pdf_plan", "pdf_audit", "manifest", "zip", "rectified_image"]
    is_draft: bool
    filename: str
    media_type: str
    sha256: str
    byte_size: int
    generator_version: str
    created_at: datetime


class ArtifactDetailOut(ArtifactOut):
    """``manifest`` is the signed manifest of the final package the artifact belongs to."""

    manifest: dict[str, Any] | None


def _artifact_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The artifact does not exist.")


def _out(db: Session, artifact: Artifact) -> ArtifactOut:
    plan = db.get(Plan, artifact.plan_id)
    case_file = db.get(CaseFile, plan.case_file_id) if plan is not None else None
    revision = db.get(Revision, artifact.revision_id) if artifact.revision_id else None
    filename = artifact_filename(
        artifact.kind,
        is_draft=artifact.is_draft,
        case_code=case_file.code if case_file is not None else "plano",
        revision_number=revision.number if revision is not None else None,
    )
    return ArtifactOut(
        id=artifact.id,
        plan_id=artifact.plan_id,
        revision_id=artifact.revision_id,
        kind=artifact.kind,  # pyright: ignore[reportArgumentType]
        is_draft=artifact.is_draft,
        filename=filename,
        media_type=artifact_media_type(artifact.kind),
        sha256=artifact.sha256,
        byte_size=artifact.byte_size,
        generator_version=artifact.generator_version,
        created_at=artifact.created_at,
    )


def _manifest(db: Session, storage: Storage, artifact: Artifact) -> dict[str, Any] | None:
    """The newest final manifest of the artifact's revision (``None`` for drafts)."""
    if not is_final_artifact(artifact):
        return None
    manifest = db.scalars(
        select(Artifact)
        .where(
            Artifact.plan_id == artifact.plan_id,
            Artifact.revision_id == artifact.revision_id,
            Artifact.kind == ArtifactKind.MANIFEST.value,
            Artifact.is_draft.is_(False),
        )
        .order_by(Artifact.created_at.desc(), Artifact.id)
        .limit(1)
    ).first()
    if manifest is None:
        return None
    try:
        return json.loads(storage.get(manifest.storage_key))
    except ObjectNotFoundError:
        logger.error("Stored manifest artifact %s is missing", manifest.id)
        return None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post(
    "/plans/{plan_id}/exports",
    status_code=202,
    dependencies=[Depends(require_csrf)],
    responses=EXPORT_RESPONSES,
)
def post_export(
    request: Request,
    body: ExportIn,
    plan: Annotated[Plan, Depends(require_plan_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
) -> JobOut:
    """Queue a draft or final export and return its job (202); poll ``GET /jobs/{id}``.

    ``draft`` needs ``edit`` and a plan in ``EDITING`` or later, and never changes the plan
    status. ``final`` needs ``deliver`` and a ``VALIDATED`` plan (409 ``V-11`` otherwise,
    ``EXPORTED`` included: reopen and validate again to export anew); when the job
    completes the plan is ``EXPORTED``. A request equal to a queued or running one returns
    that job.
    """
    needed = CasePermission.DELIVER if body.mode == "final" else CasePermission.EDIT
    case_file = db.get(CaseFile, plan.case_file_id)
    if case_file is None:  # pragma: no cover - guaranteed by the foreign key
        raise ProblemError(404, "Not Found", "The plan does not exist.")
    if needed.value not in case_permissions(db, user.id, case_file):
        raise _deny(request, db, audit_factory, user, case_file, needed.value)
    plan_id = plan.id
    try:
        job, _created = request_export(
            db,
            plan,
            user,
            mode=body.mode,
            paper=body.paper,
            scale=body.scale,
            client_ip=_client_ip(request),
        )
    except ExportConflictError as exc:
        db.rollback()
        logger.warning("Rejected %s export of plan %s: %s", body.mode, plan_id, exc.code)
        raise ProblemError(
            HTTPStatus.CONFLICT, HTTPStatus.CONFLICT.phrase, str(exc), extensions={"code": exc.code}
        ) from None
    db.commit()
    error = JobErrorOut.model_validate(job.error) if job.error is not None else None
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,  # pyright: ignore[reportArgumentType]
        source_id=job.source_id,
        error=error,
        result_ref=job.result_ref,
    )


@router.get("/plans/{plan_id}/artifacts", responses=LIST_RESPONSES)
def list_artifacts(
    plan: Annotated[Plan, Depends(require_plan_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
) -> list[ArtifactOut]:
    """Artifacts of the plan, newest first (drafts, finals and the rectified image).

    Requires ``read``.
    """
    rows = db.scalars(
        select(Artifact)
        .where(Artifact.plan_id == plan.id)
        .order_by(Artifact.created_at.desc(), Artifact.kind, Artifact.id)
    ).all()
    return [_out(db, artifact) for artifact in rows]


def _readable_artifact(
    artifact_id: str,
    request: Request,
    db: Session,
    user: User,
    audit_factory: sessionmaker[Session],
    confirm: bool,
) -> Artifact:
    try:
        artifact = db.get(Artifact, uuid.UUID(artifact_id))
    except ValueError:
        raise _artifact_not_found() from None
    if artifact is None:
        raise _artifact_not_found()
    plan = db.get(Plan, artifact.plan_id)
    case_file = db.get(CaseFile, plan.case_file_id) if plan is not None else None
    if case_file is None:  # pragma: no cover - guaranteed by the foreign keys
        raise _artifact_not_found()
    held = case_permissions(db, user.id, case_file)
    final_download = confirm and is_final_artifact(artifact)
    needed = CasePermission.DELIVER if final_download else CasePermission.READ
    if needed.value not in held:
        raise _deny(request, db, audit_factory, user, case_file, needed.value)
    return artifact


@router.get(
    "/artifacts/{artifact_id}",
    response_model=ArtifactDetailOut,
    responses=ARTIFACT_RESPONSES,
)
def get_artifact(
    artifact_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[Storage, Depends(get_storage)],
    audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
    confirm: Annotated[bool, Query()] = False,
) -> Any:
    """Without ``confirm``: metadata, SHA-256 and (final) the package manifest; no event.

    With ``confirm=true``: streams the bytes (``Content-Disposition: attachment``,
    ``X-Content-SHA256``) after writing one ``artifact.downloaded`` event. Requires
    ``read``; downloading a final artifact also requires ``deliver``.
    """
    artifact = _readable_artifact(artifact_id, request, db, user, audit_factory, confirm)
    out = _out(db, artifact)
    if not confirm:
        detail = ArtifactDetailOut(**out.model_dump(), manifest=_manifest(db, storage, artifact))
        return JSONResponse(detail.model_dump(mode="json"))
    try:
        stream = storage.open(artifact.storage_key)
    except ObjectNotFoundError:
        logger.error("Stored object of artifact %s is missing", artifact.id)
        raise ProblemError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            HTTPStatus.INTERNAL_SERVER_ERROR.phrase,
            "The stored artifact is not available.",
        ) from None
    try:
        plan = db.get(Plan, artifact.plan_id)
        assert plan is not None  # noqa: S101 - checked by _readable_artifact
        append_event(
            db,
            case_file_id=plan.case_file_id,
            kind=EventKind.ARTIFACT_DOWNLOADED,
            actor_id=user.id,
            actor_role=user.role.value,
            plan_id=plan.id,
            revision_id=artifact.revision_id,
            entity_id=artifact.id,
            after={"kind": artifact.kind, "sha256": artifact.sha256, "is_draft": artifact.is_draft},
            client_ip=_client_ip(request),
        )
        db.commit()
    except Exception:
        stream.close()
        raise
    logger.info("User %s downloaded artifact %s", user.id, artifact.id)
    headers = {
        CONTENT_SHA256_HEADER: artifact.sha256,
        "Content-Length": str(artifact.byte_size),
        "Content-Disposition": _attachment(out.filename),
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(_chunks(stream), media_type=out.media_type, headers=headers)
