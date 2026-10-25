"""Canonical event content: the exact JSON that feeds the hash chain (spec section 14.1).

The content of an event is every stored column except ``hash``, serialized with the JSON
Canonicalization Scheme (RFC 8785) through the ``rfc8785`` package. Values that JSON cannot
represent natively are normalized first, so a verifier reading the row back from the
database rebuilds byte-identical content:

- UUIDs become their lowercase hyphenated string (``str(uuid)``); a missing UUID, such as
  the ``case_file_id`` of a global auth chain event, is JSON ``null``.
- ``occurred_at`` becomes UTC ISO 8601 with microseconds and a ``Z`` suffix, matching
  PostgreSQL ``timestamptz`` precision.
- ``before`` and ``after`` are kept as JSON values. JCS rejects integers outside the
  IEEE 754 safe range and non-finite floats, so such payloads fail before the insert.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import rfc8785

from sketch2cad.db.models import Event

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def format_timestamp(value: datetime) -> str:
    """Render a timezone-aware datetime as UTC ISO 8601 with microseconds."""
    if value.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    return value.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def format_uuid(value: uuid.UUID | None) -> str | None:
    """Render a UUID as its canonical string, keeping ``None``."""
    return None if value is None else str(value)


def event_content(event: Event) -> dict[str, Any]:
    """Every stored field of ``event`` except ``hash``, as JSON-ready values."""
    return {
        "id": event.id,
        "case_file_id": format_uuid(event.case_file_id),
        "plan_id": format_uuid(event.plan_id),
        "revision_id": format_uuid(event.revision_id),
        "actor_id": format_uuid(event.actor_id),
        "actor_role": event.actor_role,
        "kind": event.kind,
        "entity_id": format_uuid(event.entity_id),
        "before": event.before,
        "after": event.after,
        "reason": event.reason,
        "client_ip": event.client_ip,
        "occurred_at": format_timestamp(event.occurred_at),
        "prev_hash": event.prev_hash,
    }


def canonical_bytes(event: Event) -> bytes:
    """RFC 8785 serialization of ``event_content(event)``."""
    return rfc8785.dumps(event_content(event))
