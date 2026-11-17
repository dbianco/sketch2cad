"""Bulk acceptance and rejection of suggestions (spec sections 8.3, 11.3; REQ-17, US-12).

:func:`bulk_review` is all or nothing: it checks every id first and raises
:class:`BulkReviewError` (nothing written) when any is invalid. Otherwise it changes every
row in the caller's transaction, appends one ``entity.reviewed`` event per changed entity
and recomputes ``plan.metric_status``.

Transitions (only suggestions, ``source.method != manual``, may be reviewed):

- ``suggested -> accepted``: ``metric_origin`` is promoted by the single rule of
  :func:`sketch2cad.entities.service.promote_reviewed` (``scaled_drawing`` in a metric
  ``scaled_drawing`` plan, else ``trace_only``).
- ``suggested -> rejected``: the entity is also soft-deleted (``deleted_at``). A rejected
  suggestion is discarded: it leaves the editor, the metric status, the snapshot and the
  exports, while the row and its events keep the history (CA-05). This matches the
  ``DELETE`` of a suggestion, which also rejects it.
- Reviewing to the state the entity already has is a no-op without an event: ``accepted``
  for a live ``accepted`` or ``corrected`` suggestion, ``rejected`` for a rejected (deleted)
  one. Any other transition is invalid: an accepted or corrected suggestion is removed
  with ``DELETE``, and a rejection is final.

The event's ``before`` and ``after`` hold ``review_status`` and ``metric_origin``;
``after`` adds ``deleted`` (true for a rejection). Geometry does not change, so it is not
repeated.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.auth.lockout import utc_now
from sketch2cad.db.models import Entity, ReviewStatus, Revision, User
from sketch2cad.entities.service import (
    REVIEWED_STATUSES,
    ensure_editable,
    promote_reviewed,
)
from sketch2cad.plans.service import active_calibration, recompute_metric_status

logger = logging.getLogger(__name__)

MAX_REVIEW_IDS: Final = 2000
"""Most ids one request may review (the entity count the editor is sized for, spec 17)."""

BULK_REVIEW_CODE: Final = "bulk_review_invalid"
NOT_IN_REVISION: Final = "not_in_revision"
"""Unknown id or entity of another revision or plan (not told apart, so nothing leaks)."""
DELETED: Final = "deleted"
NOT_A_SUGGESTION: Final = "not_a_suggestion"
INVALID_TRANSITION: Final = "invalid_transition"


class BulkReviewError(ValueError):
    """Some ids cannot be reviewed (422); ``reasons`` maps each offending id to its code."""

    def __init__(self, reasons: dict[str, str]) -> None:
        super().__init__(f"{len(reasons)} entities cannot be reviewed; nothing was changed.")
        self.reasons = reasons
        self.code = BULK_REVIEW_CODE


@dataclass(frozen=True)
class BulkReviewResult:
    """Ids changed and left unchanged (already in the target state), in request order."""

    changed: list[uuid.UUID]
    unchanged: list[uuid.UUID]
    metric_status: str


def _review_state(entity: Entity) -> dict[str, Any]:
    return {"review_status": entity.review_status, "metric_origin": entity.metric_origin}


def _check(entity: Entity | None, revision: Revision, target: str) -> str | bool:
    """Reason code why ``entity`` cannot go to ``target``, or whether it changes."""
    if entity is None or entity.revision_id != revision.id:
        return NOT_IN_REVISION
    if entity.source.get("method") == "manual":
        return NOT_A_SUGGESTION
    rejected = entity.review_status == ReviewStatus.REJECTED.value
    if entity.deleted_at is not None:
        return False if rejected and target == ReviewStatus.REJECTED.value else DELETED
    if entity.review_status == ReviewStatus.SUGGESTED.value:
        return True
    if target == ReviewStatus.ACCEPTED.value and entity.review_status in REVIEWED_STATUSES:
        return False
    return INVALID_TRANSITION


def bulk_review(
    db: Session,
    revision: Revision,
    user: User,
    ids: Sequence[uuid.UUID],
    review_status: ReviewStatus,
    client_ip: str | None = None,
) -> BulkReviewResult:
    """Accept or reject the suggestions ``ids`` of ``revision`` (module docstring rules).

    Raises :class:`~sketch2cad.entities.service.EntityConflictError` when the revision is
    not editable by ``user`` (:func:`ensure_editable`) and :class:`BulkReviewError` naming
    every offending id; both before writing anything. ``review_status`` is ``accepted`` or
    ``rejected``. The caller commits.
    """
    plan = ensure_editable(db, revision, user)
    target = review_status.value
    query = select(Entity).where(Entity.id.in_(ids)).order_by(Entity.id).with_for_update()
    rows = {entity.id: entity for entity in db.scalars(query)}
    reasons: dict[str, str] = {}
    changes: list[Entity] = []
    unchanged: list[uuid.UUID] = []
    for entity_id in ids:
        verdict = _check(rows.get(entity_id), revision, target)
        if isinstance(verdict, str):
            reasons[str(entity_id)] = verdict
        elif verdict:
            changes.append(rows[entity_id])
        else:
            unchanged.append(entity_id)
    if reasons:
        raise BulkReviewError(reasons)
    calibration = active_calibration(db, plan)
    now = utc_now()
    for entity in changes:
        before = _review_state(entity)
        entity.review_status = target
        entity.updated_at = now
        if review_status is ReviewStatus.ACCEPTED:
            promote_reviewed(entity, calibration, plan)
        else:
            entity.deleted_at = now
        db.flush()
        append_event(
            db,
            case_file_id=plan.case_file_id,
            kind=EventKind.ENTITY_REVIEWED,
            actor_id=user.id,
            actor_role=user.role.value,
            plan_id=plan.id,
            revision_id=revision.id,
            entity_id=entity.id,
            before=before,
            after={**_review_state(entity), "deleted": entity.deleted_at is not None},
            client_ip=client_ip,
        )
    result = recompute_metric_status(db, plan)
    logger.info(
        "User %s reviewed %d entities of revision %s as %s (%d unchanged)",
        user.id,
        len(changes),
        revision.id,
        target,
        len(unchanged),
    )
    return BulkReviewResult([e.id for e in changes], unchanged, result.status)
