"""Hash-chained event writer: one chain per case file (spec section 14.1, REQ-11).

``hash = SHA-256(prev_hash || JCS(event without hash))``, where ``prev_hash`` is the
64-character lowercase hex digest of the previous event of the same case file, encoded
as ASCII, and the JCS bytes come from :func:`sketch2cad.audit.canonical.canonical_bytes`.
The first event of a case file uses ``prev_hash = SHA-256(str(case_file_id))``.

Events without a case file (``case_file_id=None``: ``auth.login``, ``auth.failed``) form
one global auth chain (docs/audit.md) whose first event uses
``prev_hash = SHA-256(b"auth")``.

The writer serializes concurrent appends on the ``case_file`` row lock
(``SELECT ... FOR UPDATE``), or on the transaction-scoped advisory lock
``AUTH_CHAIN_LOCK_KEY`` for the global chain, and takes the event id from the sequence
while holding it, so ids grow along each chain and the hashed content includes the id. It
requires the default ``READ COMMITTED`` isolation: after the lock is granted, the next
statement sees the event the previous holder committed.
"""

import hashlib
import logging
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sketch2cad.audit.canonical import canonical_bytes
from sketch2cad.auth.lockout import utc_now
from sketch2cad.db.models import CaseFile, Event

logger = logging.getLogger(__name__)

# Key of the ``pg_advisory_xact_lock`` that serializes the global auth chain ("S2CAUTH").
AUTH_CHAIN_LOCK_KEY = 0x5332434155544800
AUTH_CHAIN_SEED = b"auth"


class EventKind(StrEnum):
    """Minimum event kinds of spec section 14.1."""

    CASE_FILE_CREATED = "case_file.created"
    MEMBER_ADDED = "member.added"
    SOURCE_INGESTED = "source.ingested"
    SOURCE_DOWNLOADED = "source.downloaded"
    PLAN_CREATED = "plan.created"
    CALIBRATION_PROPOSED = "calibration.proposed"
    CALIBRATION_ACCEPTED = "calibration.accepted"
    LOCAL_FRAME_SET = "local_frame.set"
    LOCAL_FRAME_CHANGED = "local_frame.changed"
    SUGGESTIONS_ADDED = "suggestions.added"
    EVIDENCE_CREATED = "evidence.created"
    EVIDENCE_UPDATED = "evidence.updated"
    ENTITY_CREATED = "entity.created"
    ENTITY_UPDATED = "entity.updated"
    ENTITY_RECOMPUTED = "entity.recomputed"
    ENTITY_REVIEWED = "entity.reviewed"
    ENTITY_DELETED = "entity.deleted"
    PLAN_TRANSITION = "plan.transition"
    REVISION_FROZEN = "revision.frozen"
    EXPORT_REQUESTED = "export.requested"
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_DOWNLOADED = "artifact.downloaded"
    LEASE_ACQUIRED = "lease.acquired"
    LEASE_RELEASED = "lease.released"
    AUTH_LOGIN = "auth.login"
    AUTH_FAILED = "auth.failed"
    ACCESS_DENIED = "access.denied"
    CONFIG_CHANGED = "config.changed"


class UnknownCaseFileError(LookupError):
    """The case file of an event does not exist."""


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def genesis_hash(case_file_id: uuid.UUID | None) -> str:
    """``prev_hash`` of the first event of a chain.

    ``SHA-256(str(case_file_id))`` for a case file, ``SHA-256(b"auth")`` for the global
    auth chain (``case_file_id`` is ``None``).
    """
    if case_file_id is None:
        return sha256_hex(AUTH_CHAIN_SEED)
    return sha256_hex(str(case_file_id).encode("ascii"))


def compute_hash(event: Event) -> str:
    """``SHA-256(prev_hash || JCS(event without hash))`` for a fully populated event."""
    return sha256_hex(event.prev_hash.encode("ascii") + canonical_bytes(event))


def lock_case_file(session: Session, case_file_id: uuid.UUID) -> None:
    """Take the row lock that serializes the chain of ``case_file_id``."""
    locked = session.scalar(
        select(CaseFile.id).where(CaseFile.id == case_file_id).with_for_update()
    )
    if locked is None:
        raise UnknownCaseFileError(f"case file {case_file_id} does not exist")


def lock_auth_chain(session: Session) -> None:
    """Take the transaction-scoped advisory lock that serializes the global auth chain."""
    session.execute(select(func.pg_advisory_xact_lock(AUTH_CHAIN_LOCK_KEY)))


def lock_chain(session: Session, case_file_id: uuid.UUID | None) -> None:
    """Serialize appends to the chain of ``case_file_id`` (``None``: global auth chain)."""
    if case_file_id is None:
        lock_auth_chain(session)
    else:
        lock_case_file(session, case_file_id)


def last_hash(session: Session, case_file_id: uuid.UUID | None) -> str | None:
    """Hash of the newest event of the chain of ``case_file_id``, or ``None`` when empty."""
    in_chain = (
        Event.case_file_id.is_(None) if case_file_id is None else Event.case_file_id == case_file_id
    )
    return session.scalar(select(Event.hash).where(in_chain).order_by(Event.id.desc()).limit(1))


def next_event_id(session: Session) -> int:
    """Draw the next id from the ``event.id`` sequence."""
    return session.execute(
        select(func.nextval(func.pg_get_serial_sequence("event", "id")))
    ).scalar_one()


def append_event(
    session: Session,
    *,
    case_file_id: uuid.UUID | None,
    kind: EventKind,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    plan_id: uuid.UUID | None = None,
    revision_id: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    client_ip: str | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    """Append one event to the chain of ``case_file_id`` in the caller's transaction.

    ``case_file_id=None`` appends to the global auth chain. Locks the case file row (or the
    auth chain advisory lock) until the caller commits or rolls back, links the event to
    the previous hash and flushes the insert. ``occurred_at`` defaults to the current UTC
    time and must be timezone-aware. Raises :class:`UnknownCaseFileError` when the case
    file does not exist and ``rfc8785.CanonicalizationError`` when ``before`` or ``after``
    cannot be canonicalized; nothing is inserted in either case.
    """
    lock_chain(session, case_file_id)
    prev_hash = last_hash(session, case_file_id) or genesis_hash(case_file_id)
    event = Event(
        id=next_event_id(session),
        case_file_id=case_file_id,
        plan_id=plan_id,
        revision_id=revision_id,
        actor_id=actor_id,
        actor_role=actor_role,
        kind=kind.value,
        entity_id=entity_id,
        before=before,
        after=after,
        reason=reason,
        client_ip=client_ip,
        occurred_at=occurred_at if occurred_at is not None else utc_now(),
        prev_hash=prev_hash,
    )
    event.hash = compute_hash(event)
    session.add(event)
    session.flush()
    logger.info("event appended id=%s case_file_id=%s kind=%s", event.id, case_file_id, kind)
    return event
