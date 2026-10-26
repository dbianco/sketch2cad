"""The package manifest of spec section 14.2, serialized with RFC 8785 (JCS).

:func:`build_manifest` returns the unsigned manifest as plain JSON values with exactly the
keys of spec 14.2 except ``signature``, which :func:`sketch2cad.package.signing.sign_manifest`
adds. Timestamps are UTC ISO 8601 with a ``Z`` suffix (microseconds only when non-zero);
hashes are lowercase hex SHA-256. ``artifacts`` keeps the caller's order: JCS sorts object
keys but not arrays, so callers pass the artifacts in a fixed order.

This module depends only on the standard library and ``rfc8785``.
"""

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import rfc8785

MANIFEST_VERSION = "2.0"
METRIC_STATUSES = frozenset({"metric", "approximate", "non_metric"})
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KEYS = (
    "manifest_version",
    "case_file",
    "plan_id",
    "revision",
    "artifacts",
    "events_chain",
    "calibration",
    "local_frame",
    "metric_status",
    "software",
    "catalog_version",
    "validated_by",
    "validated_at",
    "exported_by",
    "exported_at",
    "timezone_display",
)
"""Keys of the unsigned manifest; the signed one adds ``signature``."""


@dataclass(frozen=True)
class ArtifactEntry:
    """One file of the package: ``kind`` as in spec 14.2 (``dxf``, ``pdf_plan``, ...)."""

    kind: str
    filename: str
    sha256: str


@dataclass(frozen=True)
class Actor:
    """A user named in the manifest (``validated_by``, ``exported_by``)."""

    user_id: str
    display_name: str | None
    role: str


@dataclass(frozen=True)
class SoftwareInfo:
    """Versions that produced the artifacts (jotdown H4-9)."""

    version: str
    git_commit: str
    pipeline_version: str
    ocr_model: str | None


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def format_timestamp(value: datetime) -> str:
    """UTC ISO 8601 with a ``Z`` suffix; raises ``ValueError`` for naive datetimes."""
    if value.tzinfo is None:
        raise ValueError("manifest timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _check_sha256(value: str, name: str) -> str:
    if not SHA256_HEX.match(value):
        raise ValueError(f"{name} is not a lowercase hex SHA-256")
    return value


def _artifact_json(artifact: ArtifactEntry, index: int) -> dict[str, str]:
    name = f"artifacts[{index}]"
    if not artifact.kind:
        raise ValueError(f"{name}.kind is empty")
    filename = artifact.filename
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError(f"{name}.filename must be a plain file name")
    return {
        "kind": artifact.kind,
        "filename": filename,
        "sha256": _check_sha256(artifact.sha256, f"{name}.sha256"),
    }


def _actor_json(actor: Actor) -> dict[str, str | None]:
    return {"user_id": actor.user_id, "display_name": actor.display_name, "role": actor.role}


def build_manifest(
    *,
    case_file_id: str,
    case_file_code: str,
    plan_id: str,
    revision_number: int,
    snapshot_sha256: str,
    artifacts: Sequence[ArtifactEntry],
    events_count: int,
    events_last_hash: str,
    calibration: Mapping[str, Any] | None,
    local_frame: Mapping[str, Any] | None,
    metric_status: str,
    software: SoftwareInfo,
    catalog_version: str,
    validated_by: Actor,
    validated_at: datetime,
    exported_by: Actor,
    exported_at: datetime,
    timezone_display: str,
) -> dict[str, Any]:
    """The unsigned manifest of spec 14.2 (every key of :data:`MANIFEST_KEYS`).

    ``calibration`` and ``local_frame`` are deep-copied JSON values (``None`` when the
    plan has none). Raises ``ValueError`` on malformed hashes, file names, a duplicate
    file name, an unknown ``metric_status`` or a naive timestamp.
    """
    entries = [_artifact_json(artifact, index) for index, artifact in enumerate(artifacts)]
    filenames = [entry["filename"] for entry in entries]
    if len(set(filenames)) != len(filenames):
        raise ValueError("artifact file names must be unique")
    if metric_status not in METRIC_STATUSES:
        raise ValueError(f"metric_status must be one of {sorted(METRIC_STATUSES)}")
    if revision_number < 1 or events_count < 0:
        raise ValueError("revision_number must be positive and events_count non-negative")
    return {
        "manifest_version": MANIFEST_VERSION,
        "case_file": {"id": case_file_id, "code": case_file_code},
        "plan_id": plan_id,
        "revision": {
            "number": revision_number,
            "snapshot_sha256": _check_sha256(snapshot_sha256, "snapshot_sha256"),
        },
        "artifacts": entries,
        "events_chain": {
            "count": events_count,
            "last_hash": _check_sha256(events_last_hash, "events_last_hash"),
        },
        "calibration": copy.deepcopy(dict(calibration)) if calibration is not None else None,
        "local_frame": copy.deepcopy(dict(local_frame)) if local_frame is not None else None,
        "metric_status": metric_status,
        "software": {
            "version": software.version,
            "git_commit": software.git_commit,
            "pipeline_version": software.pipeline_version,
            "ocr_model": software.ocr_model,
        },
        "catalog_version": catalog_version,
        "validated_by": _actor_json(validated_by),
        "validated_at": format_timestamp(validated_at),
        "exported_by": _actor_json(exported_by),
        "exported_at": format_timestamp(exported_at),
        "timezone_display": timezone_display,
    }


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """RFC 8785 bytes of ``manifest`` as given (with ``signature`` if present).

    These are the bytes of ``manifest.json`` in the package; the signed message is
    :func:`sketch2cad.package.signing.signing_payload` (the same without ``signature``).
    """
    return rfc8785.dumps(dict(manifest))
