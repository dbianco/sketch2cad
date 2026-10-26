"""Plan creation and retrieval (spec sections 8.1, 9.1, 9.2; REQ-03).

A new plan enters ``CALIBRATION_PENDING`` directly: ``CREATED -> CALIBRATION_PENDING`` is
the system transition on creation (spec section 9.2), so no plan is ever left in
``CREATED``. Creation inserts revision 1 in ``draft``, points ``current_revision_id`` at it
and appends ``plan.created`` in the caller's transaction. It enqueues no job:
``suggest_lines`` is enqueued only when the calibration is accepted or ``non_metric`` is
chosen.

:func:`recompute_metric_status` refreshes ``plan.metric_status`` from the active
calibration and the live entities of the current revision (spec section 11.3); callers run
it in the same transaction as every change that can alter it (entity create, edit, delete
and recompute, calibration accept, local frame, revision freeze and reopen). It writes the
field only through :func:`~sketch2cad.geometry.metric_status.apply_metric_status`.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.db.models import (
    Calibration,
    CaseFile,
    Entity,
    MetricStatus,
    Plan,
    PlanStatus,
    Revision,
    RevisionState,
    Source,
    SourceMode,
    User,
)
from sketch2cad.entities.dimensions import DimensionTolerances, dimension_discrepancy
from sketch2cad.geometry.metric_status import (
    MetricEntity,
    MetricStatusResult,
    apply_metric_status,
    compute_metric_status,
)

logger = logging.getLogger(__name__)

FIRST_REVISION_NUMBER = 1


class ForeignSourceError(ValueError):
    """The primary source does not exist or belongs to another case file."""


@dataclass(frozen=True)
class NewPlan:
    """Validated input for a new plan."""

    title: str
    primary_source_id: uuid.UUID
    source_mode: SourceMode = SourceMode.SCHEMATIC_SKETCH


def _primary_source(db: Session, case_file: CaseFile, source_id: uuid.UUID) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.case_file_id != case_file.id:
        raise ForeignSourceError(f"source {source_id} is not a source of case {case_file.id}")
    return source


def create_plan(
    db: Session,
    case_file: CaseFile,
    user: User,
    data: NewPlan,
    client_ip: str | None = None,
) -> tuple[Plan, Revision]:
    """Insert a plan in ``CALIBRATION_PENDING`` with draft revision 1 and ``plan.created``.

    Raises :class:`ForeignSourceError` before writing anything when ``primary_source_id``
    is unknown or belongs to another case file. Runs in the caller's transaction; the
    caller commits.
    """
    source = _primary_source(db, case_file, data.primary_source_id)
    plan = Plan(
        case_file_id=case_file.id,
        title=data.title,
        primary_source_id=source.id,
        source_mode=data.source_mode,
        status=PlanStatus.CALIBRATION_PENDING,
    )
    db.add(plan)
    db.flush()
    revision = Revision(
        plan_id=plan.id,
        number=FIRST_REVISION_NUMBER,
        state=RevisionState.DRAFT.value,
        created_by=user.id,
    )
    db.add(revision)
    db.flush()
    plan.current_revision_id = revision.id
    db.flush()
    db.refresh(plan)
    db.refresh(revision)
    append_event(
        db,
        case_file_id=case_file.id,
        kind=EventKind.PLAN_CREATED,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=revision.id,
        entity_id=plan.id,
        after={
            "title": plan.title,
            "primary_source_id": str(plan.primary_source_id),
            "source_mode": plan.source_mode.value,
            "status": plan.status.value,
            "metric_status": plan.metric_status.value,
            "revision_number": revision.number,
        },
        client_ip=client_ip,
    )
    logger.info("User %s created plan %s in case file %s", user.id, plan.id, case_file.id)
    return plan, revision


def active_calibration(db: Session, plan: Plan) -> Calibration | None:
    """The accepted calibration of ``plan`` that is active, or ``None``."""
    return db.scalars(
        select(Calibration).where(Calibration.plan_id == plan.id, Calibration.is_active)
    ).one_or_none()


def current_revision(db: Session, plan: Plan) -> Revision | None:
    """The revision ``current_revision_id`` points at, or ``None`` when it is unset."""
    if plan.current_revision_id is None:
        return None
    return db.get(Revision, plan.current_revision_id)


def metric_status_of(
    db: Session, plan: Plan, tolerances: DimensionTolerances | None = None
) -> MetricStatusResult:
    """Metric status of ``plan`` from its active calibration and current live entities.

    ``tolerances`` only decides ``discrepant_dimensions`` (spec default when ``None``); the
    status never depends on it.
    """
    tolerances = tolerances or DimensionTolerances()
    calibration = active_calibration(db, plan)
    entities: list[MetricEntity] = []
    if plan.current_revision_id is not None:
        query = select(Entity).where(
            Entity.revision_id == plan.current_revision_id, Entity.deleted_at.is_(None)
        )
        entities = [
            MetricEntity(
                id=str(entity.id),
                type=entity.type,
                layer=entity.layer,
                metric_origin=entity.metric_origin,
                references_evidence=entity.evidence_id is not None,
                discrepancy=dimension_discrepancy(entity.type, entity.geometry, tolerances) is True,
            )
            for entity in db.scalars(query.order_by(Entity.id))
        ]
    return compute_metric_status(
        plan.source_mode.value,
        None if calibration is None else calibration.method,
        calibration is not None and calibration.as_metric,
        entities,
    )


def recompute_metric_status(db: Session, plan: Plan) -> MetricStatusResult:
    """Store the metric status of ``plan`` in ``plan.metric_status`` and return it.

    Appends no event. Runs in the caller's transaction (after its pending writes are
    flushed); the caller commits.
    """
    db.flush()
    result = metric_status_of(db, plan)
    apply_metric_status(plan, result, MetricStatus)
    db.flush()
    return result
