"""Canonical revision snapshot (spec section 8.3): pure functions, no database access.

The snapshot holds only values already stored on the plan, its active calibration, the
catalog and the entities, so building it twice from the same rows gives the same dict;
nothing reads the clock. Entities are the non-deleted ones, sorted by id (the canonical
UUID string, which orders like the UUID itself). Each entity follows the shape of spec
section 8.2; ``created_by`` is null for system entities, as in the entities API.

``snapshot_sha256`` is the lowercase hex SHA-256 of the RFC 8785 (JCS) bytes. JCS sorts
object keys and fixes number formatting, so the JSONB copy stored on the revision, whose
key order PostgreSQL does not keep, canonicalizes back to the same bytes. To keep that
round trip exact, every number must be finite and inside the IEEE 754 safe integer range
(``|x| < 2**53``): a larger float would come back from JSONB as an integer that JCS
rejects. :func:`build_snapshot` raises :class:`SnapshotError` otherwise.

``evidence_table`` stays empty until the evidence register (H3) exists.
"""

import hashlib
import math
from collections.abc import Iterable
from datetime import datetime
from typing import Any, cast

import rfc8785

from sketch2cad.audit.canonical import format_timestamp, format_uuid
from sketch2cad.db.models import Calibration, Entity, Plan, Revision

SCHEMA_VERSION = "2.0"
SAFE_MAGNITUDE = 2**53


class SnapshotError(ValueError):
    """The snapshot holds a value that cannot round-trip through JCS and JSONB."""


def calibration_json(calibration: Calibration | None) -> dict[str, Any] | None:
    """Copy of the calibration row; its ``local_frame`` goes to its own snapshot key."""
    if calibration is None:
        return None
    return {
        "id": str(calibration.id),
        "method": calibration.method,
        "image_to_paper": calibration.image_to_paper,
        "control_points": calibration.control_points,
        "reprojection_error_mm": calibration.reprojection_error_mm,
        "paper_scale": calibration.paper_scale,
        "as_metric": calibration.as_metric,
        "accepted_by": str(calibration.accepted_by),
        "accepted_at": _timestamp(calibration.accepted_at),
    }


def entity_json(entity: Entity, revision_number: int) -> dict[str, Any]:
    """An entity in the shape of spec section 8.2."""
    return {
        "id": str(entity.id),
        "type": entity.type,
        "layer": entity.layer,
        "geometry": entity.geometry,
        "image_region": entity.image_region,
        "attributes": entity.attributes,
        "evidence_id": format_uuid(entity.evidence_id),
        "constraint": entity.constraint,
        "source": entity.source,
        "confidence": entity.confidence,
        "model_version": entity.model_version,
        "review_status": entity.review_status,
        "metric_origin": entity.metric_origin,
        "created_by": format_uuid(entity.created_by),
        "revision_number": revision_number,
    }


def build_snapshot(
    plan: Plan,
    revision: Revision,
    calibration: Calibration | None,
    catalog_version: str,
    entities: Iterable[Entity],
) -> dict[str, Any]:
    """The snapshot of ``revision``; soft-deleted entities are left out.

    Raises :class:`SnapshotError` when a number cannot round-trip (see module docstring).
    """
    live = sorted((e for e in entities if e.deleted_at is None), key=lambda e: str(e.id))
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": str(plan.id),
        "revision_number": revision.number,
        "source_mode": plan.source_mode.value,
        "metric_status": plan.metric_status.value,
        "calibration": calibration_json(calibration),
        "local_frame": None if calibration is None else calibration.local_frame,
        "catalog_version": catalog_version,
        "entities": [entity_json(entity, revision.number) for entity in live],
        "evidence_table": [],
    }
    _check_numbers(snapshot, "$")
    return snapshot


def canonical_bytes(snapshot: dict[str, Any]) -> bytes:
    """RFC 8785 serialization of ``snapshot``."""
    return rfc8785.dumps(snapshot)


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    """Lowercase hex SHA-256 of :func:`canonical_bytes`."""
    return hashlib.sha256(canonical_bytes(snapshot)).hexdigest()


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else format_timestamp(value)


def _check_numbers(value: object, path: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int | float):
        if not math.isfinite(value) or abs(value) >= SAFE_MAGNITUDE:
            raise SnapshotError(f"{path}: {value!r} is not a finite number below 2**53")
        return
    if isinstance(value, dict):
        for key, item in cast(dict[str, object], value).items():
            _check_numbers(item, f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(cast(list[object], value)):
            _check_numbers(item, f"{path}[{index}]")
        return
    raise SnapshotError(f"{path}: {type(value).__name__} is not a JSON value")
