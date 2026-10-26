"""Worker task ``export``: draft exports and the signed final package of a plan (REQ-12..14).

The API defers it (``sketch2cad.exports.service.request_export``) with the keyword argument
``job_meta_id``; the job's ``params`` say what to export. Same lifecycle as the ``qc`` and
``rectify`` jobs: ``running`` commits first, then the artifacts, their ``artifact.created``
events, the final status and (final only) the ``VALIDATED -> EXPORTED`` transition commit
together. Expected failures end ``failed`` with a Spanish ``{cause, action}``; an
unexpected error ends ``failed`` with :data:`INTERNAL_ERROR` and is re-raised. Retries are
idempotent: a finished job is left untouched and identical bytes reuse their artifact row.

- ``draft``: ``plan.dxf`` and ``plan.pdf`` (watermarked, annex with the original or the
  rasterized page of a PDF) of the current revision, stored as ``dxf`` and ``pdf_plan``
  with ``is_draft``; ``result_ref`` is the PDF artifact. The plan status is unchanged.
- ``final``: :func:`~sketch2cad.package.build.build_package` of the frozen validated
  revision with the whole case event chain, signed with the key at ``SIGNING_KEY_PATH``;
  stores ``zip`` (``result_ref``), ``dxf``, ``pdf_plan``, ``pdf_audit`` and ``manifest``.
  The case file row stays locked from reading the chain until commit, so the package holds
  a consistent prefix of the chain.

Only ``DATABASE_URL``, ``STORAGE_ROOT``, ``SIGNING_KEY_PATH`` and the optional export
variables (``DIMENSION_DISCREPANCY_TOLERANCE_M``, ``LEGAL_DISCLAIMER_ENABLED``,
``GIT_COMMIT``) are read; the API-only settings are not required. The tolerance and the
disclaimer flag resolve with the configuration overrides (incident type of the case file,
then global) over those variables.
"""

import hashlib
import io
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import procrastinate
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

# Module import: exports.service imports the worker app, which imports this module.
import sketch2cad.exports.service as export_service
from sketch2cad.audit.canonical import event_content
from sketch2cad.audit.chain import EventKind, append_event, lock_case_file
from sketch2cad.auth.lockout import utc_now
from sketch2cad.catalog import CatalogDefinition, default_catalog
from sketch2cad.db.models import (
    Artifact,
    ArtifactKind,
    CaseFile,
    Catalog,
    Event,
    JobMeta,
    JobStatus,
    MediaType,
    Plan,
    PlanStatus,
    Revision,
    Source,
    User,
)
from sketch2cad.entities.dimensions import DimensionTolerances
from sketch2cad.export.disclaimer import ExportLegal
from sketch2cad.export.dxf import DxfExportError, export_dxf
from sketch2cad.export.pdf import PdfExportError, SheetChoice, export_pdf
from sketch2cad.export.pdf_annex import AnnexImage, AnnexImageError
from sketch2cad.export.pdf_sheet import CaseMeta
from sketch2cad.jobs.service import (
    MISSING_OBJECT,
    MISSING_SOURCE,
    TERMINAL_STATUSES,
    UNREADABLE_PDF,
    JobError,
    _finish,  # pyright: ignore[reportPrivateUsage]
    _locked_job,  # pyright: ignore[reportPrivateUsage]
    _start,  # pyright: ignore[reportPrivateUsage]
)
from sketch2cad.package.audit_report import AUDIT_FILENAME
from sketch2cad.package.build import (
    DXF_FILENAME,
    MANIFEST_FILENAME,
    PDF_FILENAME,
    PackageError,
    PackageInputs,
    SourceFile,
    build_package,
)
from sketch2cad.package.manifest import Actor
from sketch2cad.package.signing import SIGNING_KEY_ENV, SigningKeyError, load_signing_key
from sketch2cad.plans.transitions import mark_exported
from sketch2cad.revisions.service import revision_snapshot
from sketch2cad.revisions.snapshot import SnapshotError
from sketch2cad.sources.raster import RasterError, rasterize_source
from sketch2cad.storage import ObjectNotFoundError, Storage
from sketch2cad.worker import tasks_raster

logger = logging.getLogger(__name__)

INCIDENT_LABELS = {
    "traffic_collision": "Siniestro vial",
    "crime_scene": "Escena del crimen",
    "inspection": "Inspección ocular",
    "other": "Otro",
}
"""Spanish incident types of the title block (same labels as the web case form)."""

MISSING_PLAN = JobError(
    "El plano o la revisión de la exportación ya no existe.",
    "Vuelve a solicitar la exportación desde el plano.",
)
NOT_VALIDATED = JobError(
    "El plano ya no está validado en la revisión pedida; no se generó el paquete final.",
    "Valida el plano y vuelve a solicitar la exportación final.",
)
EXPORT_FAILED = JobError(
    "No se pudo generar la exportación con el contenido actual del plano.",
    "Revisa las entidades del plano (por ejemplo, cotas con extremos iguales) y vuelve a "
    "intentarlo; si el problema continúa, contacta al administrador.",
)
SIGNING_KEY_UNAVAILABLE = JobError(
    "La clave de firma del servidor no está disponible.",
    "Contacta al administrador del sistema.",
)
INTERNAL_ERROR = JobError(
    "Error interno al generar la exportación.",
    "Vuelve a intentarlo más tarde; si el problema continúa, contacta al administrador.",
)
_EXPORT_ERRORS = (DxfExportError, PdfExportError, AnnexImageError, SnapshotError, PackageError)


class ExportJobError(Exception):
    """The ``export`` job cannot produce its artifacts; carries the user-facing error."""

    def __init__(self, error: JobError, reason: str) -> None:
        super().__init__(reason)
        self.error = error


@dataclass(frozen=True)
class _Context:
    job: JobMeta
    plan: Plan
    revision: Revision
    user: User
    case_file: CaseFile
    source: Source


def _display(user: User | None) -> str:
    if user is None:
        return "-"
    return user.display_name or user.username


def _catalog(db: Session, version: str) -> CatalogDefinition:
    row = db.scalars(select(Catalog).where(Catalog.version == version)).first()
    if row is None:
        return default_catalog()  # export_dxf rejects a version mismatch
    return CatalogDefinition.model_validate(
        {"version": row.version, "layers": row.layers, "symbols": row.symbols}
    )


def _context(db: Session, job: JobMeta) -> _Context:
    params = job.params
    plan = db.get(Plan, uuid.UUID(str(params.get("plan_id"))))
    revision = db.get(Revision, uuid.UUID(str(params.get("revision_id"))))
    user = db.get(User, uuid.UUID(str(params.get("requested_by"))))
    if plan is None or revision is None or user is None or revision.plan_id != plan.id:
        raise ExportJobError(MISSING_PLAN, "job plan, revision or user does not exist")
    db.refresh(plan, with_for_update=True)
    case_file = db.get(CaseFile, plan.case_file_id)
    source = db.get(Source, plan.primary_source_id)
    if source is None or case_file is None:  # the case file is guaranteed by a foreign key
        raise ExportJobError(MISSING_SOURCE, "plan has no existing primary source")
    return _Context(job, plan, revision, user, case_file, source)


def _original(db: Session, storage: Storage, source: Source) -> SourceFile:
    """The original bytes and, for a PDF, its 300 dpi raster (made here when missing)."""
    try:
        data = storage.get(source.storage_key)
        raster = None
        if source.media_type == MediaType.PDF.value:
            with db.begin_nested():
                source = rasterize_source(db, storage, source.id)
            raster = storage.get(cast(str, source.raster_key))
    except RasterError as exc:
        raise ExportJobError(UNREADABLE_PDF, "PDF does not rasterize") from exc
    except ObjectNotFoundError as exc:
        raise ExportJobError(MISSING_OBJECT, "stored object is missing") from exc
    return SourceFile(data, source.sha256, source.media_type, source.original_filename, raster)


def _annex(original: SourceFile) -> AnnexImage:
    if original.raster_png is not None:
        digest = hashlib.sha256(original.raster_png).hexdigest()
        return AnnexImage(original.raster_png, digest, "original_page")
    return AnnexImage(original.data, original.sha256, "original")


def _case_meta(db: Session, ctx: _Context, reviewer: User | None) -> CaseMeta:
    case_file = ctx.case_file
    return CaseMeta(
        case_file.code,
        ctx.plan.title,
        INCIDENT_LABELS.get(case_file.incident_type.value, case_file.incident_type.value),
        case_file.incident_date,
        _display(db.get(User, case_file.created_by)),
        _display(reviewer) if reviewer is not None else None,
    )


def _store(
    db: Session, storage: Storage, ctx: _Context, kind: ArtifactKind, data: bytes, draft: bool
) -> Artifact:
    """Store ``data`` write-once and record its artifact row and ``artifact.created``.

    Identical bytes of the same plan, revision, kind and draft flag reuse the existing row
    and write no event.
    """
    stored = storage.put(io.BytesIO(data))
    revision_id = ctx.revision.id
    artifact = db.scalars(
        select(Artifact).where(
            Artifact.plan_id == ctx.plan.id,
            Artifact.revision_id == revision_id,
            Artifact.kind == kind.value,
            Artifact.is_draft == draft,
            Artifact.sha256 == stored.sha256,
        )
    ).first()
    if artifact is not None:
        return artifact
    artifact = Artifact(
        plan_id=ctx.plan.id,
        revision_id=revision_id,
        kind=kind.value,
        is_draft=draft,
        sha256=stored.sha256,
        storage_key=stored.key,
        byte_size=len(data),
        generator_version=export_service.generator_version(),
    )
    db.add(artifact)
    db.flush()
    append_event(
        db,
        case_file_id=ctx.plan.case_file_id,
        kind=EventKind.ARTIFACT_CREATED,
        actor_id=ctx.user.id,
        actor_role=ctx.user.role.value,
        plan_id=ctx.plan.id,
        revision_id=revision_id,
        entity_id=artifact.id,
        after={
            "kind": kind.value,
            "sha256": stored.sha256,
            "byte_size": len(data),
            "is_draft": draft,
            "job_id": str(ctx.job.id),
        },
    )
    return artifact


def _sheet(params: dict[str, Any]) -> tuple[SheetChoice, int | None]:
    paper = params.get("paper") or "auto"
    scale = params.get("scale")
    return cast(SheetChoice, paper), int(scale) if scale is not None else None


def _draft(db: Session, storage: Storage, ctx: _Context) -> Artifact:
    revision = ctx.revision
    snapshot = revision.snapshot or revision_snapshot(db, ctx.plan, revision)
    original = _original(db, storage, ctx.source)
    sheet, scale = _sheet(ctx.job.params)
    tolerances = DimensionTolerances.resolve(db, ctx.case_file.incident_type)
    version = export_service.generator_version()
    catalog = _catalog(db, str(snapshot["catalog_version"]))
    try:
        dxf = export_dxf(
            snapshot,
            catalog,
            draft=True,
            generator_version=version,
            frozen_at=None,
            dimension_tolerances=tolerances,
        )
        pdf = export_pdf(
            snapshot,
            catalog,
            case_meta=_case_meta(db, ctx, None),
            draft=True,
            generator_version=version,
            frozen_at=None,
            sheet=sheet,
            scale=scale,
            dimension_tolerances=tolerances,
            annex=_annex(original),
            legal=ExportLegal.resolve(db),
        )
    except _EXPORT_ERRORS as exc:
        raise ExportJobError(EXPORT_FAILED, f"draft export failed: {type(exc).__name__}") from exc
    _store(db, storage, ctx, ArtifactKind.DXF, dxf, True)
    return _store(db, storage, ctx, ArtifactKind.PDF_PLAN, pdf, True)


def _case_events(db: Session, case_file_id: uuid.UUID) -> list[dict[str, Any]]:
    """The whole chain of the case file, as hashed (and as ``GET /events`` serializes it)."""
    lock_case_file(db, case_file_id)
    rows = db.scalars(select(Event).where(Event.case_file_id == case_file_id).order_by(Event.id))
    return [{**event_content(event), "hash": event.hash} for event in rows]


def _validation(db: Session, revision: Revision) -> tuple[User | None, datetime]:
    """Who froze ``revision`` and when (its ``revision.frozen`` event)."""
    event = db.scalars(
        select(Event)
        .where(Event.revision_id == revision.id, Event.kind == EventKind.REVISION_FROZEN.value)
        .order_by(Event.id.desc())
        .limit(1)
    ).first()
    frozen_at = cast(datetime, revision.frozen_at)
    if event is None or event.actor_id is None:
        return None, frozen_at
    return db.get(User, event.actor_id), event.occurred_at


def _actor(user: User | None) -> Actor:
    if user is None:
        return Actor("unknown", None, "unknown")
    return Actor(str(user.id), user.display_name or user.username, user.role.value)


def _signing_key_path() -> str:
    path = os.environ.get(SIGNING_KEY_ENV, "")
    if not path:
        raise ExportJobError(SIGNING_KEY_UNAVAILABLE, f"{SIGNING_KEY_ENV} is not set")
    return path


def _final(db: Session, storage: Storage, ctx: _Context) -> Artifact:
    plan, revision = ctx.plan, ctx.revision
    if plan.status is not PlanStatus.VALIDATED or plan.validated_revision_id != revision.id:
        raise ExportJobError(NOT_VALIDATED, f"plan is {plan.status.value}, not validated")
    if revision.snapshot is None or revision.snapshot_sha256 is None:
        raise ExportJobError(NOT_VALIDATED, "validated revision is not frozen")
    try:
        key = load_signing_key(_signing_key_path())
    except SigningKeyError as exc:
        raise ExportJobError(SIGNING_KEY_UNAVAILABLE, "signing key cannot be loaded") from exc
    original = _original(db, storage, ctx.source)
    validator, validated_at = _validation(db, revision)
    snapshot = revision.snapshot
    sheet, scale = _sheet(ctx.job.params)
    inputs = PackageInputs(
        snapshot=snapshot,
        snapshot_sha256=revision.snapshot_sha256,
        catalog=_catalog(db, str(snapshot["catalog_version"])),
        case_meta=_case_meta(db, ctx, validator),
        case_file_id=str(plan.case_file_id),
        plan_id=str(plan.id),
        revision_id=str(revision.id),
        frozen_at=revision.frozen_at,
        validated_by=_actor(validator),
        validated_at=validated_at,
        exported_by=_actor(ctx.user),
        exported_at=utc_now(),
        source=original,
        events=_case_events(db, plan.case_file_id),
        calibration=snapshot.get("calibration"),
        local_frame=snapshot.get("local_frame"),
        software=export_service.software_info(),
        signing_key=key,
        timezone_display=export_service.TIMEZONE_DISPLAY,
        legal=ExportLegal.resolve(db),
        dimension_tolerances=DimensionTolerances.resolve(db, ctx.case_file.incident_type),
        sheet=sheet,
        scale=scale,
    )
    try:
        package = build_package(inputs)
    except _EXPORT_ERRORS as exc:
        raise ExportJobError(EXPORT_FAILED, f"package build failed: {exc}") from exc
    files = package.files
    package_artifact = _store(db, storage, ctx, ArtifactKind.ZIP, package.zip_bytes, False)
    for kind, name in (
        (ArtifactKind.DXF, DXF_FILENAME),
        (ArtifactKind.PDF_PLAN, PDF_FILENAME),
        (ArtifactKind.PDF_AUDIT, AUDIT_FILENAME),
        (ArtifactKind.MANIFEST, MANIFEST_FILENAME),
    ):
        _store(db, storage, ctx, kind, files[name], False)
    mark_exported(db, plan, ctx.user)
    return package_artifact


def _run(db: Session, storage: Storage, job: JobMeta) -> Artifact:
    ctx = _context(db, job)
    if job.params.get("mode") == "final":
        return _final(db, storage, ctx)
    return _draft(db, storage, ctx)


def _record_internal_failure(factory: sessionmaker[Session], job_id: uuid.UUID) -> None:
    with factory() as db, db.begin():
        job = _locked_job(db, job_id)
        if job.status not in TERMINAL_STATUSES:
            _finish(job, JobStatus.FAILED, INTERNAL_ERROR)


def run_export_job(factory: sessionmaker[Session], storage: Storage, job_id: uuid.UUID) -> str:
    """Run the ``export`` job ``job_id`` and return its final status (module docstring)."""
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
                with db.begin_nested():
                    artifact = _run(db, storage, job)
            except ExportJobError as exc:
                _finish(job, JobStatus.FAILED, exc.error)
                logger.warning("Export job %s failed: %s", job.id, exc)
                return job.status
            job.result_ref = str(artifact.id)
            _finish(job, JobStatus.SUCCEEDED)
            logger.info("Export job %s stored artifact %s", job.id, artifact.id)
            return job.status
    except Exception:
        logger.exception("Export job %s failed unexpectedly", job_id)
        _record_internal_failure(factory, job_id)
        raise


def export(job_meta_id: str) -> str:
    """Procrastinate task body; returns the final ``job_meta`` status."""
    logger.info("Export job %s started", job_meta_id)
    return run_export_job(
        tasks_raster._session_factory(),  # pyright: ignore[reportPrivateUsage]
        tasks_raster._storage(),  # pyright: ignore[reportPrivateUsage]
        uuid.UUID(job_meta_id),
    )


def register(app: procrastinate.App) -> None:
    """Register :data:`~sketch2cad.exports.service.EXPORT_TASK_NAME` on ``app``."""
    app.task(name=export_service.EXPORT_TASK_NAME)(export)
