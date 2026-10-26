"""Export requests and artifact metadata (spec sections 9.1, 9.2, 13; REQ-12..REQ-14, V-11).

:func:`request_export` checks the plan status and queues one ``export`` job (``job_meta``
plus the Procrastinate task :data:`EXPORT_TASK_NAME`, deferred on the caller's connection
so both commit together) and appends ``export.requested``. The case permission is checked
by the caller: ``edit`` for a draft, ``deliver`` for a final export.

- ``draft``: allowed from ``EDITING`` onward; the job exports the plan's current revision
  as it is when the job runs (snapshot built on the fly, not frozen) and never changes the
  plan status.
- ``final``: only from ``VALIDATED`` (V-11); the job packages the frozen snapshot of
  ``validated_revision_id`` and moves the plan to ``EXPORTED``.

``job_meta.params`` holds ``mode``, ``paper``, ``scale``, ``plan_id``, ``revision_id`` and
``requested_by``; ``job_meta.source_id`` is the plan's primary source, so ``GET /jobs/{id}``
applies the same ``read`` check as for the other jobs. While a final export of the plan is
queued or running, another final request returns that job instead of queuing a second one;
a draft request returns the queued or running draft job with the same parameters.
"""

import logging
import os
import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

import sketch2cad.worker.app as worker_app
from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.db.models import Artifact, ArtifactKind, JobMeta, JobStatus, Plan, PlanStatus, User
from sketch2cad.package.manifest import SoftwareInfo

logger = logging.getLogger(__name__)

EXPORT_JOB_KIND = "export"
EXPORT_TASK_NAME = "export"
"""Procrastinate task name of the export job (``sketch2cad.worker.tasks_export``)."""

ExportMode = Literal["draft", "final"]
PaperChoice = Literal["A4", "A3", "auto"]
ExportScale = Literal[50, 100, 200, 250, 500]

V11_CODE = "V-11"
V11_MESSAGE = "La exportación final requiere un plano validado."
DRAFT_CODE = "draft_not_allowed"
DRAFT_MESSAGE = "La exportación borrador requiere un plano calibrado, en edición o posterior."
DRAFT_STATUSES = frozenset(
    {
        PlanStatus.EDITING,
        PlanStatus.REVIEW_PENDING,
        PlanStatus.IN_REVIEW,
        PlanStatus.VALIDATED,
        PlanStatus.EXPORTED,
    }
)
_ACTIVE_STATUSES = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)

PACKAGE_NAME = "sketch2cad"
GIT_COMMIT_ENV = "GIT_COMMIT"
PIPELINE_VERSION = "none"
"""No suggestion pipeline runs in the MVP yet; the manifest says so explicitly."""
TIMEZONE_DISPLAY = "UTC"
"""Display time zone of the manifest (spec section 19 default)."""

_FILES: dict[str, tuple[str, str]] = {
    ArtifactKind.DXF.value: ("plano.dxf", "application/dxf"),
    ArtifactKind.PDF_PLAN.value: ("plano.pdf", "application/pdf"),
    ArtifactKind.PDF_AUDIT.value: ("auditoria.pdf", "application/pdf"),
    ArtifactKind.MANIFEST.value: ("manifest.json", "application/json"),
    ArtifactKind.ZIP.value: ("paquete.zip", "application/zip"),
    ArtifactKind.RECTIFIED_IMAGE.value: ("rectificada.png", "image/png"),
}
FINAL_KINDS = frozenset(
    {
        ArtifactKind.DXF.value,
        ArtifactKind.PDF_PLAN.value,
        ArtifactKind.PDF_AUDIT.value,
        ArtifactKind.MANIFEST.value,
        ArtifactKind.ZIP.value,
    }
)


class ExportConflictError(Exception):
    """The plan status does not allow the export (409); ``code`` names the rule."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def software_info() -> SoftwareInfo:
    """Versions written in the manifest (jotdown H4-9); ``GIT_COMMIT`` comes from the image."""
    try:
        package_version = version(PACKAGE_NAME)
    except PackageNotFoundError:  # pragma: no cover - the package is always installed
        package_version = "0.0.0"
    return SoftwareInfo(
        package_version, os.environ.get(GIT_COMMIT_ENV) or "unknown", PIPELINE_VERSION, None
    )


def generator_version() -> str:
    """``generator_version`` of export artifacts, the same string the exporters embed."""
    return software_info().version


def is_final_artifact(artifact: Artifact) -> bool:
    """Final export artifacts need ``deliver`` to be downloaded (spec section 9.1)."""
    return not artifact.is_draft and artifact.kind in FINAL_KINDS


def artifact_media_type(kind: str) -> str:
    return _FILES.get(kind, ("artefacto.bin", "application/octet-stream"))[1]


def artifact_filename(
    kind: str, *, is_draft: bool, case_code: str, revision_number: int | None
) -> str:
    """Download name, for example ``EXP-2026-0001_r1_plano_borrador.pdf``."""
    name = _FILES.get(kind, ("artefacto.bin", ""))[0]
    stem, _, extension = name.rpartition(".")
    revision = f"_r{revision_number}" if revision_number is not None else ""
    draft = "_borrador" if is_draft else ""
    return f"{case_code}{revision}_{stem}{draft}.{extension}"


def _active_job(db: Session, plan: Plan, mode: str, params: dict[str, object]) -> JobMeta | None:
    query = select(JobMeta).where(
        JobMeta.plan_id == plan.id,
        JobMeta.kind == EXPORT_JOB_KIND,
        JobMeta.status.in_(_ACTIVE_STATUSES),
        JobMeta.params["mode"].astext == mode,
    )
    jobs = db.scalars(query.order_by(JobMeta.id)).all()
    if mode == "final":
        return jobs[0] if jobs else None
    keys = ("paper", "scale", "revision_id")
    return next((job for job in jobs if all(job.params.get(k) == params[k] for k in keys)), None)


def request_export(
    db: Session,
    plan: Plan,
    user: User,
    *,
    mode: ExportMode,
    paper: PaperChoice = "auto",
    scale: int | None = None,
    client_ip: str | None = None,
) -> tuple[JobMeta, bool]:
    """Queue the export of ``plan`` in the caller's transaction; return ``(job, created)``.

    Locks the plan row. Raises :class:`ExportConflictError` (V-11 for a final export of a
    plan that is not ``VALIDATED``) before writing anything. ``created`` is false when an
    equivalent queued or running job is returned instead; no event is written then.
    """
    db.refresh(plan, with_for_update=True)
    if mode == "final":
        if plan.status is not PlanStatus.VALIDATED or plan.validated_revision_id is None:
            raise ExportConflictError(V11_MESSAGE, V11_CODE)
        revision_id = plan.validated_revision_id
    else:
        if plan.status not in DRAFT_STATUSES or plan.current_revision_id is None:
            raise ExportConflictError(DRAFT_MESSAGE, DRAFT_CODE)
        revision_id = plan.current_revision_id
    params: dict[str, object] = {
        "mode": mode,
        "paper": paper,
        "scale": scale,
        "plan_id": str(plan.id),
        "revision_id": str(revision_id),
        "requested_by": str(user.id),
    }
    existing = _active_job(db, plan, mode, params)
    if existing is not None:
        logger.info("Export job %s already covers the request on plan %s", existing.id, plan.id)
        return existing, False
    job = JobMeta(
        id=uuid.uuid4(),
        plan_id=plan.id,
        source_id=plan.primary_source_id,
        kind=EXPORT_JOB_KIND,
        params=params,
        status=JobStatus.QUEUED.value,
    )
    db.add(job)
    db.flush()
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=EventKind.EXPORT_REQUESTED,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=revision_id,
        entity_id=job.id,
        after={"job_id": str(job.id), "mode": mode, "paper": paper, "scale": scale},
        client_ip=client_ip,
    )
    connection = db.connection().connection.driver_connection
    worker_app.app.configure_task(EXPORT_TASK_NAME, connection=connection).defer(
        job_meta_id=str(job.id)
    )
    logger.info("Queued %s export job %s for plan %s", mode, job.id, plan.id)
    return job, True
