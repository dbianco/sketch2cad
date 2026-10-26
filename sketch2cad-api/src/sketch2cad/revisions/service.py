"""Freeze the current revision of a plan and reopen it as a new draft (spec 8.3, 9.2; REQ-10).

These are building blocks of the plan transitions: ``IN_REVIEW -> VALIDATED`` calls
:func:`freeze` and ``VALIDATED|EXPORTED -> REOPENED`` calls :func:`reopen`. The plan
status, ``validated_revision_id``, the lease and the permissions belong to the caller;
both functions lock the plan row (``FOR UPDATE``), run in the caller's transaction and
leave the commit to it.

Freezing is one ``UPDATE`` that sets ``state``, ``snapshot``, ``snapshot_sha256`` and
``frozen_at`` together, as the ``ck_revision_frozen_snapshot`` check and the
``revision_frozen_immutable`` trigger require. Reopening never touches the frozen
revision: it inserts revision ``n + 1`` as a draft with ``parent_revision_id`` and copies
its live entities under new ids, keeping their provenance (``source``, ``review_status``,
``metric_origin``, ``confidence``, ``model_version``, ``created_by``). Constraint
references (``constraint.refs``) are remapped to the copies; ``source`` stays as it was.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.auth.lockout import utc_now
from sketch2cad.catalog import default_catalog
from sketch2cad.db.models import (
    Catalog,
    Entity,
    Evidence,
    Plan,
    Revision,
    RevisionState,
    User,
)
from sketch2cad.plans.service import active_calibration, recompute_metric_status
from sketch2cad.revisions.snapshot import build_snapshot, snapshot_sha256

logger = logging.getLogger(__name__)


class RevisionConflictError(Exception):
    """The revision cannot be frozen or the plan cannot be reopened now (409)."""


@dataclass(frozen=True)
class Reopened:
    """The new draft revision and the ids of the copied entities (old id -> new id)."""

    revision: Revision
    entity_ids: dict[uuid.UUID, uuid.UUID]


def _locked_plan(db: Session, plan_id: uuid.UUID) -> Plan:
    plan = db.get(Plan, plan_id)
    if plan is None:  # pragma: no cover - guaranteed by the foreign key
        raise RevisionConflictError(f"plan {plan_id} does not exist")
    db.refresh(plan, with_for_update=True)
    return plan


def active_catalog_version(db: Session) -> str:
    """Version of the active catalog; ``default-1`` when none is active."""
    version = db.scalar(select(Catalog.version).where(Catalog.is_active))
    return version if version is not None else default_catalog().version


def revision_snapshot(db: Session, plan: Plan, revision: Revision) -> dict[str, Any]:
    """The spec section 8.3 snapshot of ``revision`` from the current database rows."""
    entities = db.scalars(select(Entity).where(Entity.revision_id == revision.id))
    evidence = db.scalars(select(Evidence).where(Evidence.case_file_id == plan.case_file_id))
    return build_snapshot(
        plan,
        revision,
        active_calibration(db, plan),
        active_catalog_version(db),
        entities,
        evidence,
    )


def _append(
    db: Session,
    plan: Plan,
    revision: Revision,
    user: User,
    kind: EventKind,
    after: dict[str, Any],
    client_ip: str | None,
) -> None:
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=kind,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=revision.id,
        entity_id=revision.id,
        after=after,
        client_ip=client_ip,
    )


def freeze(db: Session, revision: Revision, user: User, client_ip: str | None = None) -> Revision:
    """Freeze the current draft ``revision`` of its plan and append ``revision.frozen``.

    Raises :class:`RevisionConflictError` when the revision is not a draft or is not the
    current revision of its plan, and :class:`~sketch2cad.revisions.snapshot.SnapshotError`
    when a value cannot be canonicalized; nothing is written in either case.
    """
    plan = _locked_plan(db, revision.plan_id)
    db.refresh(revision)
    if revision.state != RevisionState.DRAFT.value:
        raise RevisionConflictError(f"Revision {revision.number} is already frozen.")
    if plan.current_revision_id != revision.id:
        raise RevisionConflictError(f"Revision {revision.number} is not the current revision.")
    recompute_metric_status(db, plan)  # the snapshot records it
    snapshot = revision_snapshot(db, plan, revision)
    digest = snapshot_sha256(snapshot)
    frozen = db.execute(
        update(Revision)
        .where(Revision.id == revision.id, Revision.state == RevisionState.DRAFT.value)
        .values(
            state=RevisionState.FROZEN.value,
            snapshot=snapshot,
            snapshot_sha256=digest,
            frozen_at=utc_now(),
        )
        .returning(Revision.id)
    ).scalar_one_or_none()
    if frozen is None:  # pragma: no cover - excluded by the plan lock
        raise RevisionConflictError(f"Revision {revision.number} is already frozen.")
    db.refresh(revision)
    _append(
        db,
        plan,
        revision,
        user,
        EventKind.REVISION_FROZEN,
        {
            "revision_number": revision.number,
            "snapshot_sha256": digest,
            "entity_count": len(snapshot["entities"]),
        },
        client_ip,
    )
    logger.info("User %s froze revision %s of plan %s", user.id, revision.id, plan.id)
    return revision


def _copy(entity: Entity, revision_id: uuid.UUID, new_id: uuid.UUID) -> Entity:
    return Entity(
        id=new_id,
        revision_id=revision_id,
        type=entity.type,
        layer=entity.layer,
        geometry=entity.geometry,
        image_region=entity.image_region,
        attributes=entity.attributes,
        evidence_id=entity.evidence_id,
        constraint=entity.constraint,
        source=entity.source,
        confidence=entity.confidence,
        model_version=entity.model_version,
        review_status=entity.review_status,
        metric_origin=entity.metric_origin,
        created_by=entity.created_by,
        updated_at=entity.updated_at,
    )


def _remap_constraint(
    constraint: dict[str, Any] | None, ids: dict[str, str]
) -> dict[str, Any] | None:
    if constraint is None or not isinstance(constraint.get("refs"), list):
        return constraint
    refs = [ids.get(str(ref), ref) for ref in constraint["refs"]]
    return {**constraint, "refs": refs}


def reopen(db: Session, plan: Plan, user: User, client_ip: str | None = None) -> Reopened:
    """Create draft revision ``n + 1`` from the frozen current revision ``n``.

    Copies the live entities of ``n`` under new ids, points ``current_revision_id`` at the
    new draft and appends ``revision.reopened``. Raises :class:`RevisionConflictError`
    when the current revision is not frozen.
    """
    plan = _locked_plan(db, plan.id)
    parent = db.get(Revision, plan.current_revision_id) if plan.current_revision_id else None
    if parent is None or parent.state != RevisionState.FROZEN.value:
        raise RevisionConflictError("Only a plan whose current revision is frozen can reopen.")
    last = db.scalar(select(func.max(Revision.number)).where(Revision.plan_id == plan.id))
    child = Revision(
        plan_id=plan.id,
        number=(last or parent.number) + 1,
        parent_revision_id=parent.id,
        state=RevisionState.DRAFT.value,
        created_by=user.id,
    )
    db.add(child)
    db.flush()
    query = select(Entity).where(Entity.revision_id == parent.id, Entity.deleted_at.is_(None))
    originals = list(db.scalars(query.order_by(Entity.id)))
    entity_ids = {entity.id: uuid.uuid4() for entity in originals}
    text_ids = {str(old): str(new) for old, new in entity_ids.items()}
    copies = [_copy(entity, child.id, entity_ids[entity.id]) for entity in originals]
    for copy in copies:
        copy.constraint = _remap_constraint(copy.constraint, text_ids)
    db.add_all(copies)
    plan.current_revision_id = child.id
    recompute_metric_status(db, plan)
    db.refresh(child)
    _append(
        db,
        plan,
        child,
        user,
        EventKind.REVISION_REOPENED,
        {
            "revision_number": child.number,
            "parent_revision_id": str(parent.id),
            "parent_revision_number": parent.number,
            "entity_ids": text_ids,
        },
        client_ip,
    )
    logger.info(
        "User %s reopened plan %s as revision %s with %d entities",
        user.id,
        plan.id,
        child.number,
        len(copies),
    )
    return Reopened(child, entity_ids)
