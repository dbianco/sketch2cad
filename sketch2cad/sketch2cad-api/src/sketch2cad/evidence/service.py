"""Evidence register of a case file (spec sections 2.1, 8.1 and 9.1, REQ-08, US-10).

Evidence is numbered once per case file, so evidence no. 7 is the same in every plan
that places it (an entity whose ``evidence_id`` points here). Numbers start at 1, follow
each other without gaps and are never reused: there is no deletion, and ``number`` never
changes after creation. :func:`create_evidence` serializes concurrent creations on the
``case_file`` row lock (``SELECT ... FOR UPDATE``, the same lock the event chain takes)
and draws ``max(number) + 1`` while holding it; under ``READ COMMITTED`` the statement
after the lock sees the evidence the previous holder committed.
``uq_evidence_case_file_id_number`` stays as the last line of defence.

Every mutation appends ``evidence.created`` (``after``) or ``evidence.updated``
(``before`` and ``after``) to the case chain in the caller's transaction, with
``plan_id = null`` and ``entity_id`` = the evidence id. The caller commits.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event, lock_case_file
from sketch2cad.db.models import CaseFile, Evidence, User

logger = logging.getLogger(__name__)


def evidence_state(evidence: Evidence) -> dict[str, Any]:
    """JSON state of ``evidence`` recorded in its events."""
    return {
        "number": evidence.number,
        "label": evidence.label,
        "attributes": evidence.attributes,
    }


def list_evidence(db: Session, case_file: CaseFile) -> list[Evidence]:
    """Evidence of ``case_file`` ordered by number."""
    query = select(Evidence).where(Evidence.case_file_id == case_file.id)
    return list(db.scalars(query.order_by(Evidence.number)))


def find_case_evidence(
    db: Session, case_file_id: uuid.UUID, evidence_id: uuid.UUID
) -> Evidence | None:
    """Evidence ``evidence_id`` if it belongs to ``case_file_id``, else ``None``.

    Missing evidence and evidence of another case file look the same, so callers cannot
    probe ids across case files.
    """
    evidence = db.get(Evidence, evidence_id)
    if evidence is None or evidence.case_file_id != case_file_id:
        return None
    return evidence


def next_evidence_number(db: Session, case_file_id: uuid.UUID) -> int:
    """``max(number) + 1`` of the case file (1 when empty); call with the case row locked."""
    current = db.scalar(
        select(func.max(Evidence.number)).where(Evidence.case_file_id == case_file_id)
    )
    return 1 if current is None else current + 1


def _record(
    db: Session,
    evidence: Evidence,
    user: User,
    kind: EventKind,
    client_ip: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    append_event(
        db,
        case_file_id=evidence.case_file_id,
        kind=kind,
        actor_id=user.id,
        actor_role=user.role.value,
        entity_id=evidence.id,
        before=before,
        after=after,
        client_ip=client_ip,
    )


def create_evidence(
    db: Session,
    case_file: CaseFile,
    user: User,
    label: str,
    attributes: dict[str, Any],
    client_ip: str | None = None,
) -> Evidence:
    """Insert evidence with the next number of ``case_file`` and append ``evidence.created``.

    Holds the ``case_file`` row lock until the caller's transaction ends.
    """
    lock_case_file(db, case_file.id)
    evidence = Evidence(
        id=uuid.uuid4(),
        case_file_id=case_file.id,
        number=next_evidence_number(db, case_file.id),
        label=label,
        attributes=dict(attributes),
        created_by=user.id,
    )
    db.add(evidence)
    db.flush()
    db.refresh(evidence)
    _record(
        db, evidence, user, EventKind.EVIDENCE_CREATED, client_ip, after=evidence_state(evidence)
    )
    logger.info(
        "User %s created evidence %s no. %d in case file %s",
        user.id,
        evidence.id,
        evidence.number,
        case_file.id,
    )
    return evidence


def update_evidence(
    db: Session,
    evidence: Evidence,
    user: User,
    label: str | None = None,
    attributes: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> Evidence:
    """Replace ``label`` and/or ``attributes`` and append ``evidence.updated``.

    ``number`` never changes. Locks the case file row first, then re-reads the evidence,
    so ``before`` is the state the previous edit committed.
    """
    lock_case_file(db, evidence.case_file_id)
    db.refresh(evidence)
    before = evidence_state(evidence)
    if label is not None:
        evidence.label = label
    if attributes is not None:
        evidence.attributes = dict(attributes)
    db.flush()
    _record(
        db,
        evidence,
        user,
        EventKind.EVIDENCE_UPDATED,
        client_ip,
        before=before,
        after=evidence_state(evidence),
    )
    logger.info("User %s updated evidence %s", user.id, evidence.id)
    return evidence
