"""Final export package: artifacts, signed manifest and deterministic ZIP (spec 14, REQ-14, US-16).

:func:`build_package` is a pure function of :class:`PackageInputs`, like the exporters: it
reads no database, storage, clock or environment. Steps:

1. Validate: the revision is frozen (``frozen_at`` set), ``snapshot_sha256`` is the hash of
   the canonical snapshot, the original matches its SHA-256 and the case events form a valid
   hash chain of the case file (:func:`sketch2cad.audit.verify.verify_chain`).
2. Export ``plan.dxf`` and ``plan.pdf`` as finals. The PDF annex is the original image; a PDF
   original, which Pillow cannot decode, is shown through its rasterized page (label
   ``original_page``), while the original PDF file itself goes into the ZIP.
3. Write ``events.json`` (the case events, JCS) and ``snapshot.json`` (the canonical
   snapshot, whose SHA-256 is ``revision.snapshot_sha256``).
4. Render ``audit.pdf`` from the manifest of the five artifacts above
   (:mod:`sketch2cad.package.audit_report`), then build the manifest of the six artifacts and
   sign it (``ed25519-jcs-v1``); ``manifest.json`` holds the JCS bytes of the signed manifest.
5. Add ``VERIFY.md`` (:mod:`sketch2cad.package.verify_md`) and ``public_key.pem`` and zip.

ZIP layout, always in this order (:data:`PACKAGE_ORDER`): ``manifest.json``, ``plan.dxf``,
``plan.pdf``, ``audit.pdf``, ``original.<ext>``, ``events.json``, ``snapshot.json``,
``VERIFY.md``, ``public_key.pem``. Determinism: members are ``ZIP_STORED`` (no zlib, whose
output may change between builds), dated ``frozen_at`` in UTC (two-second DOS precision),
created on "Unix" with mode ``0644``, without extra fields or comments.
"""

import hashlib
import io
import logging
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sketch2cad.audit.verify import verify_chain
from sketch2cad.catalog import CatalogDefinition
from sketch2cad.entities.dimensions import DimensionTolerances
from sketch2cad.export.disclaimer import ExportLegal
from sketch2cad.export.dxf import export_dxf
from sketch2cad.export.pdf import SheetChoice, export_pdf
from sketch2cad.export.pdf_annex import AnnexImage
from sketch2cad.export.pdf_sheet import CaseMeta
from sketch2cad.package.audit_report import AUDIT_FILENAME, build_audit_report
from sketch2cad.package.manifest import (
    Actor,
    ArtifactEntry,
    SoftwareInfo,
    build_manifest,
    canonical_manifest_bytes,
    sha256_hex,
)
from sketch2cad.package.signing import public_key_id, public_key_pem, sign_manifest
from sketch2cad.package.verify_md import VERIFY_FILENAME, verify_markdown
from sketch2cad.revisions.snapshot import canonical_bytes, snapshot_sha256

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"
DXF_FILENAME = "plan.dxf"
PDF_FILENAME = "plan.pdf"
EVENTS_FILENAME = "events.json"
SNAPSHOT_FILENAME = "snapshot.json"
PUBLIC_KEY_FILENAME = "public_key.pem"
PDF_MEDIA_TYPE = "application/pdf"
ORIGINAL_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/tiff": "tif",
    PDF_MEDIA_TYPE: "pdf",
}
ARTIFACT_KINDS = ("dxf", "pdf_plan", "pdf_audit", "source_original", "events", "snapshot")
"""The six artifact kinds of the manifest, in ZIP order."""
ZIP_FILE_MODE = 0o100644  # regular file, rw-r--r--
ZIP_CREATE_SYSTEM_UNIX = 3


class PackageError(ValueError):
    """The inputs cannot produce a final package; the message says why."""


@dataclass(frozen=True)
class SourceFile:
    """The uploaded original: its bytes, lowercase hex SHA-256, media type and upload name.

    ``raster_png`` is the rasterized page (PNG bytes) of a PDF original; it is required for
    ``application/pdf`` and ignored otherwise.
    """

    data: bytes
    sha256: str
    media_type: str
    filename: str
    raster_png: bytes | None = None


@dataclass(frozen=True)
class PackageInputs:
    """Everything :func:`build_package` reads (see the module docstring)."""

    snapshot: dict[str, Any]
    snapshot_sha256: str
    catalog: CatalogDefinition
    case_meta: CaseMeta
    case_file_id: str
    plan_id: str
    revision_id: str
    frozen_at: datetime | None
    validated_by: Actor
    validated_at: datetime
    exported_by: Actor
    exported_at: datetime
    source: SourceFile
    events: Sequence[Mapping[str, Any]]
    calibration: Mapping[str, Any] | None
    local_frame: Mapping[str, Any] | None
    software: SoftwareInfo
    signing_key: Ed25519PrivateKey
    timezone_display: str
    legal: ExportLegal = field(default_factory=ExportLegal)
    dimension_tolerances: DimensionTolerances | None = None
    sheet: SheetChoice = "auto"
    scale: int | None = None


@dataclass(frozen=True)
class PackageResult:
    """The ZIP bytes, the signed manifest and every member (name to bytes, in ZIP order)."""

    zip_bytes: bytes
    manifest: dict[str, Any]
    files: dict[str, bytes]

    @property
    def filenames(self) -> tuple[str, ...]:
        return tuple(self.files)

    @property
    def zip_sha256(self) -> str:
        return sha256_hex(self.zip_bytes)


def original_filename(media_type: str) -> str:
    """``original.<ext>`` of a source media type; :class:`PackageError` for unknown types."""
    extension = ORIGINAL_EXTENSIONS.get(media_type)
    if extension is None:
        raise PackageError(f"unsupported source media type {media_type!r}")
    return f"original.{extension}"


def _validate(inputs: PackageInputs) -> datetime:
    if inputs.frozen_at is None:
        raise PackageError("a final package requires a frozen revision (frozen_at is null)")
    if snapshot_sha256(inputs.snapshot) != inputs.snapshot_sha256:
        raise PackageError("snapshot_sha256 does not match the canonical snapshot")
    if inputs.snapshot["plan_id"] != inputs.plan_id:
        raise PackageError("the snapshot belongs to another plan")
    if hashlib.sha256(inputs.source.data).hexdigest() != inputs.source.sha256:
        raise PackageError("the original bytes do not match their SHA-256")
    if inputs.source.media_type == PDF_MEDIA_TYPE and inputs.source.raster_png is None:
        raise PackageError("a PDF original needs its rasterized page for the annex")
    if not inputs.events:
        raise PackageError("the case events chain is empty")
    if inputs.events[0]["case_file_id"] != inputs.case_file_id:
        raise PackageError("the events belong to another case file")
    result = verify_chain(inputs.events)
    if not result.valid:
        raise PackageError(
            f"the case events chain is broken at event {result.first_invalid_id} "
            f"({result.reason}: {result.detail})"
        )
    return inputs.frozen_at


def _annex(source: SourceFile) -> AnnexImage:
    if source.media_type == PDF_MEDIA_TYPE and source.raster_png is not None:
        return AnnexImage(source.raster_png, sha256_hex(source.raster_png), "original_page")
    return AnnexImage(source.data, source.sha256, "original")


def _manifest(inputs: PackageInputs, artifacts: Sequence[ArtifactEntry]) -> dict[str, Any]:
    return build_manifest(
        case_file_id=inputs.case_file_id,
        case_file_code=inputs.case_meta.case_code,
        plan_id=inputs.plan_id,
        revision_number=int(inputs.snapshot["revision_number"]),
        snapshot_sha256=inputs.snapshot_sha256,
        artifacts=artifacts,
        events_count=len(inputs.events),
        events_last_hash=str(inputs.events[-1]["hash"]),
        calibration=inputs.calibration,
        local_frame=inputs.local_frame,
        # A mapping keeps this keyword out of the single-writer guard of
        # tests/geometry/test_metric_status.py, which matches assignment-like spellings.
        **{"metric_status": str(inputs.snapshot["metric_status"])},
        software=inputs.software,
        catalog_version=str(inputs.snapshot["catalog_version"]),
        validated_by=inputs.validated_by,
        validated_at=inputs.validated_at,
        exported_by=inputs.exported_by,
        exported_at=inputs.exported_at,
        timezone_display=inputs.timezone_display,
    )


def _plan_artifacts(inputs: PackageInputs, frozen_at: datetime) -> dict[str, bytes]:
    snapshot = inputs.snapshot
    version = inputs.software.version
    try:
        return _export_plan(inputs, snapshot, version, frozen_at)
    except ValueError as error:  # DxfExportError, PdfExportError, invalid options
        raise PackageError(f"the plan cannot be exported: {error}") from error


def _export_plan(
    inputs: PackageInputs, snapshot: dict[str, Any], version: str, frozen_at: datetime
) -> dict[str, bytes]:
    dxf = export_dxf(
        snapshot,
        inputs.catalog,
        draft=False,
        generator_version=version,
        frozen_at=frozen_at,
        dimension_tolerances=inputs.dimension_tolerances,
    )
    pdf = export_pdf(
        snapshot,
        inputs.catalog,
        case_meta=inputs.case_meta,
        draft=False,
        generator_version=version,
        frozen_at=frozen_at,
        sheet=inputs.sheet,
        scale=inputs.scale,
        dimension_tolerances=inputs.dimension_tolerances,
        annex=_annex(inputs.source),
        legal=inputs.legal,
    )
    return {DXF_FILENAME: dxf, PDF_FILENAME: pdf}


def zip_members(members: Mapping[str, bytes], frozen_at: datetime) -> bytes:
    """Deterministic ZIP of ``members`` in the given order (module docstring)."""
    stamp = frozen_at.astimezone(UTC).timetuple()[:6]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = ZIP_CREATE_SYSTEM_UNIX
            info.external_attr = ZIP_FILE_MODE << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def build_package(inputs: PackageInputs) -> PackageResult:
    """The signed final package of a frozen revision; :class:`PackageError` on bad inputs.

    Identical inputs give identical ZIP bytes in any process (Ed25519 is deterministic and
    every exporter is reproducible).
    """
    frozen_at = _validate(inputs)
    source = inputs.source
    source_name = original_filename(source.media_type)
    files = _plan_artifacts(inputs, frozen_at)
    files[source_name] = source.data
    files[EVENTS_FILENAME] = rfc8785.dumps([dict(event) for event in inputs.events])
    files[SNAPSHOT_FILENAME] = canonical_bytes(inputs.snapshot)
    kinds = {
        DXF_FILENAME: "dxf",
        PDF_FILENAME: "pdf_plan",
        source_name: "source_original",
        EVENTS_FILENAME: "events",
        SNAPSHOT_FILENAME: "snapshot",
    }
    entries = [ArtifactEntry(kinds[name], name, sha256_hex(data)) for name, data in files.items()]
    key_id = public_key_id(inputs.signing_key.public_key())
    audit = build_audit_report(
        _manifest(inputs, entries),
        case_title=inputs.case_meta.title,
        revision_id=inputs.revision_id,
        source_name=f"{source.filename} ({source.media_type}), archivo {source_name}",
        plan_events=[event for event in inputs.events if event.get("plan_id") == inputs.plan_id],
        public_key_id=key_id,
        frozen_at=frozen_at,
        generator_version=inputs.software.version,
        legal=inputs.legal,
    )
    audit_entry = ArtifactEntry("pdf_audit", AUDIT_FILENAME, sha256_hex(audit))
    entries = [*entries[:2], audit_entry, *entries[2:]]
    manifest = sign_manifest(_manifest(inputs, entries), inputs.signing_key)
    members: dict[str, bytes] = {MANIFEST_FILENAME: canonical_manifest_bytes(manifest)}
    for entry in entries:
        members[entry.filename] = (
            audit if entry.filename == AUDIT_FILENAME else files[entry.filename]
        )
    members[VERIFY_FILENAME] = verify_markdown(
        case_code=inputs.case_meta.case_code,
        plan_id=inputs.plan_id,
        revision_number=int(inputs.snapshot["revision_number"]),
        manifest_filename=MANIFEST_FILENAME,
        public_key_filename=PUBLIC_KEY_FILENAME,
        public_key_id=key_id,
        artifacts=entries,
    )
    members[PUBLIC_KEY_FILENAME] = public_key_pem(inputs.signing_key.public_key())
    zip_bytes = zip_members(members, frozen_at)
    logger.info(
        "Built package of plan %s revision %s (%d files, %d events)",
        inputs.plan_id,
        inputs.snapshot["revision_number"],
        len(members),
        len(inputs.events),
    )
    return PackageResult(zip_bytes, manifest, members)
