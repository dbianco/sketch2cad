"""Entity create, read, edit and soft delete with provenance (spec sections 8.2, 9.1, 14.1).

Every mutation appends its ``entity.created``, ``entity.updated`` or ``entity.deleted``
event in the caller's transaction. Events carry the entity state (:func:`entity_state`):
``after`` for a creation, ``before`` and ``after`` for an edit (``after`` adds the
``operation``), ``before`` for a deletion. ``source`` is set once on creation and never
changed by an edit. Entities are edited only in a ``draft`` revision of a plan in
``EDITING`` or ``IN_REVIEW`` (the reviewer edits while holding the lease, CU-04).
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.auth.lockout import utc_now
from sketch2cad.catalog import default_catalog
from sketch2cad.db.models import (
    Catalog,
    Entity,
    MetricOrigin,
    Plan,
    PlanStatus,
    ReviewStatus,
    Revision,
    RevisionState,
    SourceMode,
    User,
)
from sketch2cad.entities.schemas import EntityIn, EntityPatch, Geometry, SymbolGeometry
from sketch2cad.plans.service import active_calibration

logger = logging.getLogger(__name__)

EDITABLE_STATUSES = frozenset({PlanStatus.EDITING, PlanStatus.IN_REVIEW})
VALIDATED_STATUSES = frozenset({PlanStatus.VALIDATED, PlanStatus.EXPORTED})
V10_MESSAGE = "Este plano está validado. Reábrelo para editarlo."
# L_SHEET holds the title block and frame, drawn only on export (spec section 12.1).
EXPORT_ONLY_LAYERS = frozenset({"L_SHEET"})


class EntityConflictError(Exception):
    """The revision or plan does not allow edits now (409); ``code`` names the V-xx rule."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class EntityInputError(ValueError):
    """The entity refers to something the active catalog lacks, or changes its kind (422)."""


class EntityGoneError(LookupError):
    """The entity is soft-deleted (404)."""


@dataclass(frozen=True)
class CatalogRefs:
    """Names an entity may refer to: drawing layers and symbol ids of the active catalog."""

    layers: frozenset[str]
    symbol_ids: frozenset[str]


def active_catalog_refs(db: Session) -> CatalogRefs:
    """Layers and symbols of the active catalog; ``default-1`` when none is active."""
    row = db.scalars(select(Catalog).where(Catalog.is_active)).one_or_none()
    if row is None:
        catalog = default_catalog()
        layers = [layer.name for layer in catalog.layers]
        symbols = [symbol.symbol_id for symbol in catalog.symbols]
    else:
        layers = [str(layer["name"]) for layer in row.layers]
        symbols = [str(symbol["symbol_id"]) for symbol in row.symbols]
    return CatalogRefs(frozenset(layers) - EXPORT_ONLY_LAYERS, frozenset(symbols))


def _check_refs(refs: CatalogRefs, layer: str, geometry: Geometry | None) -> None:
    if layer not in refs.layers:
        raise EntityInputError(f"Layer {layer} is not a drawing layer of the active catalog.")
    if isinstance(geometry, SymbolGeometry) and geometry.symbol_id not in refs.symbol_ids:
        raise EntityInputError(f"Symbol {geometry.symbol_id} is not in the active catalog.")


def ensure_editable(db: Session, revision: Revision) -> Plan:
    """The plan of ``revision`` if its entities may be edited now, else raise a conflict.

    Single gate of every entity mutation: the edit lease check (task 054) belongs here.
    """
    plan = db.get(Plan, revision.plan_id)
    if plan is None:  # pragma: no cover - guaranteed by the foreign key
        raise EntityConflictError("The revision has no plan.")
    if plan.status in VALIDATED_STATUSES:
        raise EntityConflictError(V10_MESSAGE, code="V-10")
    if plan.status not in EDITABLE_STATUSES:
        raise EntityConflictError(f"A plan in status {plan.status.value} cannot be edited.")
    if revision.state != RevisionState.DRAFT.value:
        raise EntityConflictError(f"Revision {revision.number} is frozen and cannot be edited.")
    return plan


def entity_state(entity: Entity) -> dict[str, Any]:
    """JSON state of ``entity`` recorded in its events (ids and timestamps left out)."""
    return {
        "type": entity.type,
        "layer": entity.layer,
        "geometry": entity.geometry,
        "image_region": entity.image_region,
        "attributes": entity.attributes,
        "evidence_id": None if entity.evidence_id is None else str(entity.evidence_id),
        "constraint": entity.constraint,
        "source": entity.source,
        "confidence": entity.confidence,
        "model_version": entity.model_version,
        "review_status": entity.review_status,
        "metric_origin": entity.metric_origin,
    }


def list_entities(db: Session, revision: Revision) -> list[Entity]:
    """Entities of ``revision`` that are not soft-deleted, ordered by id."""
    query = select(Entity).where(Entity.revision_id == revision.id, Entity.deleted_at.is_(None))
    return list(db.scalars(query.order_by(Entity.id)))


def initial_metric_origin(db: Session, plan: Plan) -> MetricOrigin:
    """``scaled_drawing`` for hand-drawn entities of a metric scaled drawing, else ``none``.

    Spec section 11.3: in ``scaled_drawing`` with a calibration accepted as metric, entities
    drawn by hand are ``scaled_drawing``; otherwise a hand-placed entity without a
    dimension counts as ``none``.
    """
    calibration = active_calibration(db, plan)
    if plan.source_mode is SourceMode.SCALED_DRAWING and calibration and calibration.as_metric:
        return MetricOrigin.SCALED_DRAWING
    return MetricOrigin.NONE


def _record(
    db: Session,
    plan: Plan,
    entity: Entity,
    user: User,
    kind: EventKind,
    client_ip: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=kind,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=entity.revision_id,
        entity_id=entity.id,
        before=before,
        after=after,
        reason=reason,
        client_ip=client_ip,
    )


def create_entities(
    db: Session,
    revision: Revision,
    user: User,
    items: Sequence[EntityIn],
    client_ip: str | None = None,
) -> list[Entity]:
    """Insert hand-drawn entities with ``source.method = manual`` and one event each.

    Raises before writing anything: :class:`EntityConflictError` when the revision is not
    editable, :class:`EntityInputError` naming the first item whose layer or symbol is not
    in the active catalog. The caller commits.
    """
    plan = ensure_editable(db, revision)
    refs = active_catalog_refs(db)
    for index, item in enumerate(items):
        try:
            _check_refs(refs, item.layer, item.geometry)
        except EntityInputError as exc:
            raise EntityInputError(f"entities[{index}]: {exc}") from None
    origin = initial_metric_origin(db, plan).value
    now = utc_now()
    entities = [
        Entity(
            id=uuid.uuid4(),
            revision_id=revision.id,
            type=item.type.value,
            layer=item.layer,
            geometry=item.geometry.model_dump(mode="json"),
            attributes=dict(item.attributes),
            source={"method": "manual", "job_id": None, "parent_entity_id": None},
            review_status=ReviewStatus.ACCEPTED.value,
            metric_origin=origin,
            created_by=user.id,
            updated_at=now,
        )
        for item in items
    ]
    db.add_all(entities)
    db.flush()
    for entity in entities:
        _record(
            db, plan, entity, user, EventKind.ENTITY_CREATED, client_ip, after=entity_state(entity)
        )
    logger.info("User %s created %d entities in revision %s", user.id, len(entities), revision.id)
    return entities


def _lock_live(db: Session, entity: Entity) -> tuple[Plan, Revision]:
    """Check the entity is live and editable, then lock its row for the rest of the edit."""
    if entity.deleted_at is not None:
        raise EntityGoneError(f"entity {entity.id} is deleted")
    revision = db.get(Revision, entity.revision_id)
    if revision is None:  # pragma: no cover - guaranteed by the foreign key
        raise EntityGoneError(f"entity {entity.id} has no revision")
    plan = ensure_editable(db, revision)
    db.refresh(entity, with_for_update=True)
    if entity.deleted_at is not None:
        raise EntityGoneError(f"entity {entity.id} is deleted")
    return plan, revision


def update_entity(
    db: Session,
    entity: Entity,
    user: User,
    patch: EntityPatch,
    client_ip: str | None = None,
) -> Entity:
    """Apply ``patch`` and append ``entity.updated`` with ``operation`` and ``reason``.

    ``source``, ``review_status`` and ``metric_origin`` are left unchanged. Raises
    :class:`EntityGoneError`, :class:`EntityConflictError` or :class:`EntityInputError`
    (a geometry of another kind, or a layer or symbol not in the active catalog).
    """
    plan, _ = _lock_live(db, entity)
    if patch.geometry is not None and patch.geometry.kind != entity.type:
        raise EntityInputError(f"geometry.kind must stay {entity.type}.")
    _check_refs(active_catalog_refs(db), patch.layer or entity.layer, patch.geometry)
    before = entity_state(entity)
    if patch.geometry is not None:
        entity.geometry = patch.geometry.model_dump(mode="json")
    if patch.attributes is not None:
        entity.attributes = dict(patch.attributes)
    if patch.layer is not None:
        entity.layer = patch.layer
    entity.updated_at = utc_now()
    db.flush()
    after = {**entity_state(entity), "operation": patch.operation.value}
    _record(
        db,
        plan,
        entity,
        user,
        EventKind.ENTITY_UPDATED,
        client_ip,
        before=before,
        after=after,
        reason=patch.reason,
    )
    logger.info("User %s applied %s to entity %s", user.id, patch.operation.value, entity.id)
    return entity


def delete_entity(
    db: Session,
    entity: Entity,
    user: User,
    reason: str | None = None,
    client_ip: str | None = None,
) -> Entity:
    """Soft-delete ``entity`` (``deleted_at``) and append ``entity.deleted`` with ``before``."""
    plan, _ = _lock_live(db, entity)
    before = entity_state(entity)
    entity.deleted_at = entity.updated_at = utc_now()
    db.flush()
    _record(
        db, plan, entity, user, EventKind.ENTITY_DELETED, client_ip, before=before, reason=reason
    )
    logger.info("User %s deleted entity %s", user.id, entity.id)
    return entity
