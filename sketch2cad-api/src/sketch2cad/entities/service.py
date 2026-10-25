"""Entity create, read, edit and soft delete with provenance (spec sections 8.2, 9.1, 14.1).

Every mutation appends its ``entity.created``, ``entity.updated`` or ``entity.deleted``
event in the caller's transaction. Events carry the entity state (:func:`entity_state`):
``after`` for a creation, ``before`` and ``after`` for an edit (``after`` adds the
``operation``), ``before`` for a deletion. ``source`` is set once on creation and never
changed by an edit. Every mutation also recomputes ``plan.metric_status`` in the same
transaction (spec section 11.3, REQ-07). ``fix_length`` and the declared segment lengths
it records live in :mod:`sketch2cad.entities.ops_fix_length`; any later geometry edit
drops the declarations it breaks and updates ``metric_origin`` accordingly.
``triangulate`` and ``baseline_offset`` (:func:`_apply_constraint`) place a point or symbol
from two metric references of the same revision, store its ``constraint`` and set
``metric_origin``; moving such an entity by hand needs ``confirm_constraint_removal``
(409 ``constraint_removal_required`` otherwise), then drops the constraint and sets
``metric_origin = none`` (spec section 11.3). After any edit or deletion that changes an
entity, the entities that depend on it through ``constraint.refs`` are recomputed
(:mod:`sketch2cad.entities.recompute`), each with an ``entity.recomputed`` event in the
same transaction. Entities are edited only in a ``draft`` revision of a plan in
``EDITING`` or ``IN_REVIEW``, by the user holding the plan's edit lease (CU-04).
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
    Calibration,
    Catalog,
    Entity,
    MetricOrigin,
    Plan,
    PlanStatus,
    ReviewStatus,
    Revision,
    RevisionState,
    User,
)
from sketch2cad.entities.constraints import (
    V07_MESSAGE,
    ConstraintOperationError,
    constraint_to_json,
    point_position,
    with_position,
)
from sketch2cad.entities.ops_baseline import baseline_offset_position
from sketch2cad.entities.ops_fix_length import (
    DECLARED_SEGMENTS,
    FixLengthError,
    declared_origin,
    fix_length,
    retained_attributes,
)
from sketch2cad.entities.ops_triangulate import triangulate_position
from sketch2cad.entities.recompute import CONSTRAINT_ERROR, constraint_graph, recompute_dependents
from sketch2cad.entities.schemas import (
    Attributes,
    EntityIn,
    EntityOperation,
    EntityPatch,
    Geometry,
    SymbolGeometry,
)
from sketch2cad.geometry.constraints import would_create_cycle
from sketch2cad.geometry.homography import Point
from sketch2cad.geometry.metric_status import METRIC_ORIGINS, default_metric_origin
from sketch2cad.plans.lease import LeaseConflictError, ensure_lease_holder
from sketch2cad.plans.service import active_calibration, recompute_metric_status

logger = logging.getLogger(__name__)

EDITABLE_STATUSES = frozenset({PlanStatus.EDITING, PlanStatus.IN_REVIEW})
VALIDATED_STATUSES = frozenset({PlanStatus.VALIDATED, PlanStatus.EXPORTED})
V10_MESSAGE = "Este plano está validado. Reábrelo para editarlo."
CONSTRAINT_REMOVAL_MESSAGE = (
    "Este punto está fijado por una restricción métrica. Si lo mueves a mano se eliminará "
    "la restricción y dejará de ser métrico. Confirma para continuar."
)
"""409 ``constraint_removal_required``: a constrained entity moved without confirmation."""
# Attributes managed by the server; requests may not set them and relabels keep them.
SERVER_ATTRIBUTES = (DECLARED_SEGMENTS, CONSTRAINT_ERROR)
# L_SHEET holds the title block and frame, drawn only on export (spec section 12.1).
EXPORT_ONLY_LAYERS = frozenset({"L_SHEET"})


class EntityConflictError(Exception):
    """The revision or plan does not allow edits now (409); ``code`` names the V-xx rule.

    ``extensions`` are extra problem details members (the lease ``holder``).
    """

    def __init__(
        self, message: str, code: str | None = None, extensions: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extensions = dict(extensions or {})


class EntityInputError(ValueError):
    """The entity refers to something the active catalog lacks, or changes its kind (422).

    ``code`` names the V-xx rule when there is one: ``V-04`` for a distance, ``V-06`` for
    circles that do not cross, ``V-07`` for a reference that is not metric or depends on
    the entity being fixed.
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class EntityGoneError(LookupError):
    """The entity is soft-deleted (404)."""


@dataclass(frozen=True)
class EntityUpdate:
    """An edited entity; ``candidates`` and ``chosen`` are set by ``triangulate`` only."""

    entity: Entity
    candidates: tuple[Point, ...] | None = None
    chosen: int | None = None


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


def _check_attributes(attributes: Attributes | None) -> None:
    if attributes is None:
        return
    if DECLARED_SEGMENTS in attributes:
        raise EntityInputError(f"attributes.{DECLARED_SEGMENTS} is set only by fix_length.")
    if CONSTRAINT_ERROR in attributes:
        raise EntityInputError(f"attributes.{CONSTRAINT_ERROR} is set only by the server.")


def _check_refs(refs: CatalogRefs, layer: str, geometry: Geometry | None) -> None:
    if layer not in refs.layers:
        raise EntityInputError(f"Layer {layer} is not a drawing layer of the active catalog.")
    if isinstance(geometry, SymbolGeometry) and geometry.symbol_id not in refs.symbol_ids:
        raise EntityInputError(f"Symbol {geometry.symbol_id} is not in the active catalog.")


def ensure_editable(db: Session, revision: Revision, user: User) -> Plan:
    """The plan of ``revision`` if ``user`` may edit its entities now, else raise a conflict.

    Single gate of every content edit of a plan. Locks the plan row (``FOR UPDATE``) until
    the caller's transaction ends, so the lease cannot change hands mid-edit. Checks the
    plan status, that the revision is a draft and that ``user`` holds a valid edit lease
    (``code`` ``V-12`` with ``holder`` when another user holds it, else
    ``lease_required``).
    """
    plan = db.get(Plan, revision.plan_id)
    if plan is None:  # pragma: no cover - guaranteed by the foreign key
        raise EntityConflictError("The revision has no plan.")
    db.refresh(plan, with_for_update=True)
    if plan.status in VALIDATED_STATUSES:
        raise EntityConflictError(V10_MESSAGE, code="V-10")
    if plan.status not in EDITABLE_STATUSES:
        raise EntityConflictError(f"A plan in status {plan.status.value} cannot be edited.")
    if revision.state != RevisionState.DRAFT.value:
        raise EntityConflictError(f"Revision {revision.number} is frozen and cannot be edited.")
    try:
        ensure_lease_holder(db, plan, user)
    except LeaseConflictError as exc:
        raise EntityConflictError(str(exc), code=exc.code, extensions=exc.extensions) from None
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


def initial_metric_origin(
    plan: Plan, calibration: Calibration | None, entity_type: str
) -> MetricOrigin:
    """``scaled_drawing`` for hand-drawn entities of a metric scaled drawing, else ``none``.

    Spec section 11.3: in ``scaled_drawing`` with a calibration accepted as metric, entities
    drawn by hand are ``scaled_drawing``; otherwise a hand-placed entity without a
    dimension counts as ``none`` (:func:`default_metric_origin`). ``calibration`` is the
    plan's active calibration.
    """
    return MetricOrigin(
        default_metric_origin(
            plan.source_mode.value,
            None if calibration is None else calibration.method,
            calibration is not None and calibration.as_metric,
            entity_type,
        )
    )


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
    plan = ensure_editable(db, revision, user)
    refs = active_catalog_refs(db)
    for index, item in enumerate(items):
        try:
            _check_refs(refs, item.layer, item.geometry)
            _check_attributes(item.attributes)
        except EntityInputError as exc:
            raise EntityInputError(f"entities[{index}]: {exc}") from None
    calibration = active_calibration(db, plan)
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
            metric_origin=initial_metric_origin(plan, calibration, item.type.value).value,
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
    recompute_metric_status(db, plan)
    logger.info("User %s created %d entities in revision %s", user.id, len(entities), revision.id)
    return entities


def _lock_live(db: Session, entity: Entity, user: User) -> tuple[Plan, Revision]:
    """Check the entity is live and editable, then lock its row for the rest of the edit."""
    if entity.deleted_at is not None:
        raise EntityGoneError(f"entity {entity.id} is deleted")
    revision = db.get(Revision, entity.revision_id)
    if revision is None:  # pragma: no cover - guaranteed by the foreign key
        raise EntityGoneError(f"entity {entity.id} has no revision")
    plan = ensure_editable(db, revision, user)
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
) -> EntityUpdate:
    """Apply ``patch`` and append ``entity.updated`` with ``operation`` and ``reason``.

    ``source`` and ``review_status`` are left unchanged; ``metric_origin`` changes with
    the declared segment lengths (:func:`_apply_declarations`), with a constraint
    operation (:func:`_apply_constraint`) and when a constrained entity is moved by hand
    (:func:`_drop_moved_constraint`). Raises :class:`EntityGoneError`,
    :class:`EntityConflictError` or :class:`EntityInputError` (a geometry of another kind,
    a layer or symbol not in the active catalog, a ``fix_length``, ``triangulate`` or
    ``baseline_offset`` that cannot be applied, with ``code`` when a V-xx rule applies;
    :class:`EntityConflictError` ``constraint_removal_required`` when a constrained entity
    would move without ``confirm_constraint_removal``) before changing anything. When the
    geometry changed, the dependents are recomputed (:func:`_recompute_dependents`).
    """
    plan, _ = _lock_live(db, entity, user)
    if patch.geometry is not None and patch.geometry.kind != entity.type:
        raise EntityInputError(f"geometry.kind must stay {entity.type}.")
    _check_refs(active_catalog_refs(db), patch.layer or entity.layer, patch.geometry)
    _check_attributes(patch.attributes)
    _check_constraint_removal(entity, patch)
    before = entity_state(entity)
    old_geometry, old_attributes = entity.geometry, entity.attributes
    update = EntityUpdate(entity)
    if patch.operation in CONSTRAINT_OPERATIONS:
        update = _apply_constraint(db, entity, patch)
    if patch.operation is EntityOperation.FIX_LENGTH:
        assert patch.anchor_vertex is not None and patch.value_m is not None  # noqa: S101
        try:
            fixed = fix_length(
                entity.geometry,
                entity.attributes,
                patch.anchor_vertex,
                patch.free_vertex,
                patch.value_m,
            )
        except FixLengthError as exc:
            raise EntityInputError(str(exc), code=exc.code) from None
        entity.geometry, entity.attributes = fixed.geometry, fixed.attributes
    if patch.geometry is not None:
        entity.geometry = patch.geometry.model_dump(mode="json")
    if patch.attributes is not None:
        entity.attributes = _keep_declarations(patch.attributes, old_attributes)
    if patch.layer is not None:
        entity.layer = patch.layer
    if patch.operation not in CONSTRAINT_OPERATIONS:
        _drop_moved_constraint(entity, old_geometry)
    _apply_declarations(db, plan, entity, old_geometry, patch.operation)
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
    if entity.geometry != old_geometry:
        _recompute_dependents(db, plan, entity, user, client_ip)
    recompute_metric_status(db, plan)
    logger.info("User %s applied %s to entity %s", user.id, patch.operation.value, entity.id)
    return update


CONSTRAINT_OPERATIONS = frozenset({EntityOperation.TRIANGULATE, EntityOperation.BASELINE_OFFSET})
CONSTRAINT_ORIGINS = {
    EntityOperation.TRIANGULATE: MetricOrigin.TRIANGULATION,
    EntityOperation.BASELINE_OFFSET: MetricOrigin.BASELINE_OFFSET,
}


def _apply_constraint(db: Session, entity: Entity, patch: EntityPatch) -> EntityUpdate:
    """Place ``entity`` by ``triangulate`` or ``baseline_offset`` and store its constraint.

    Validates the references (:func:`_reference_positions`) and computes the position
    before writing anything, so a rejection leaves the entity unchanged.
    """
    position = point_position(entity.geometry)
    if position is None:
        raise EntityInputError(f"{patch.operation.value} applies to points and symbols only.")
    if patch.operation is EntityOperation.TRIANGULATE:
        assert patch.ref_a and patch.ref_b and patch.d_a_m is not None  # noqa: S101
        assert patch.d_b_m is not None  # noqa: S101
        a, b = _reference_positions(db, entity, (patch.ref_a, patch.ref_b))
        try:
            fixed = triangulate_position(
                position, a, b, patch.d_a_m, patch.d_b_m, patch.solution or "nearest"
            )
        except ConstraintOperationError as exc:
            raise EntityInputError(str(exc), code=exc.code) from None
        point, constraint = fixed.point, fixed.constraint
        update = EntityUpdate(entity, fixed.candidates, fixed.chosen)
    else:
        assert patch.base_a and patch.base_b and patch.side is not None  # noqa: S101
        assert patch.s_m is not None and patch.d_m is not None  # noqa: S101
        a, b = _reference_positions(db, entity, (patch.base_a, patch.base_b))
        try:
            placed = baseline_offset_position(a, b, patch.s_m, patch.d_m, patch.side)
        except ConstraintOperationError as exc:
            raise EntityInputError(str(exc), code=exc.code) from None
        point, constraint = placed.point, placed.constraint
        update = EntityUpdate(entity)
    entity.geometry = with_position(entity.geometry, point)
    entity.constraint = constraint_to_json(constraint)
    entity.metric_origin = CONSTRAINT_ORIGINS[patch.operation].value
    entity.attributes = _without_constraint_error(entity.attributes)
    return update


def _without_constraint_error(attributes: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attributes.items() if k != CONSTRAINT_ERROR}


def _check_constraint_removal(entity: Entity, patch: EntityPatch) -> None:
    """A hand edit may move a constrained entity only with ``confirm_constraint_removal``."""
    if entity.constraint is None or patch.geometry is None or patch.confirm_constraint_removal:
        return
    new_position = point_position(patch.geometry.model_dump(mode="json"))
    if new_position != point_position(entity.geometry):
        raise EntityConflictError(CONSTRAINT_REMOVAL_MESSAGE, code="constraint_removal_required")


def _recompute_dependents(
    db: Session, plan: Plan, changed: Entity, user: User, client_ip: str | None
) -> None:
    """Recompute the dependents of ``changed`` with one ``entity.recomputed`` event each.

    ``after`` adds ``trigger_entity_id`` and ``constraint_error`` (``None`` on success).
    """
    for item in recompute_dependents(db, changed, entity_state):
        item.entity.updated_at = utc_now()
        after = {
            **entity_state(item.entity),
            "trigger_entity_id": str(changed.id),
            "constraint_error": item.error,
        }
        _record(
            db,
            plan,
            item.entity,
            user,
            EventKind.ENTITY_RECOMPUTED,
            client_ip,
            before=item.before,
            after=after,
        )
        if item.error is not None:
            logger.warning(
                "Entity %s could not be recomputed after %s changed: %s",
                item.entity.id,
                changed.id,
                item.error,
            )


def _reference_positions(
    db: Session, entity: Entity, ref_ids: tuple[uuid.UUID, uuid.UUID]
) -> list[tuple[uuid.UUID, Point]]:
    """``(id, position)`` of each reference after checking it may constrain ``entity``.

    A reference must be a live entity of the same revision, must not be ``entity`` nor
    depend on it through ``constraint.refs`` (no cycles) and must be a point or symbol
    whose ``metric_origin`` is metric; the last two give ``code`` ``V-07``.
    """
    refs: list[Entity] = []
    for ref_id in ref_ids:
        ref = db.get(Entity, ref_id)
        if ref is None or ref.revision_id != entity.revision_id or ref.deleted_at is not None:
            raise EntityInputError(f"Reference {ref_id} is not a live entity of this revision.")
        refs.append(ref)
    if would_create_cycle(constraint_graph(db, entity.revision_id), entity.id, ref_ids):
        raise EntityInputError(V07_MESSAGE, code="V-07")
    positions: list[tuple[uuid.UUID, Point]] = []
    for ref in refs:
        position = point_position(ref.geometry)
        if position is None:
            raise EntityInputError(f"Reference {ref.id} must be a point or a symbol.")
        if ref.metric_origin not in METRIC_ORIGINS:
            raise EntityInputError(V07_MESSAGE, code="V-07")
        positions.append((ref.id, position))
    return positions


def _drop_moved_constraint(entity: Entity, old_geometry: dict[str, Any]) -> None:
    """A confirmed hand edit that moves a constrained entity removes its constraint.

    Spec section 11.3: the position no longer follows the references, so ``metric_origin``
    becomes ``none``. Edits that keep the position (rotation, attributes, layer) keep the
    constraint; :func:`_check_constraint_removal` has already required the confirmation.
    """
    if entity.constraint is None:
        return
    if point_position(entity.geometry) == point_position(old_geometry):
        return
    entity.constraint = None
    entity.metric_origin = MetricOrigin.NONE.value
    entity.attributes = _without_constraint_error(entity.attributes)


def _keep_declarations(attributes: Attributes, current: dict[str, Any]) -> dict[str, Any]:
    """New ``attributes`` of a relabel; the server-managed declarations are carried over."""
    result: dict[str, Any] = dict(attributes)
    for name in SERVER_ATTRIBUTES:
        if name in current:
            result[name] = current[name]
    return result


def _apply_declarations(
    db: Session,
    plan: Plan,
    entity: Entity,
    old_geometry: dict[str, Any],
    operation: EntityOperation,
) -> None:
    """Drop declarations a geometry edit broke and derive ``metric_origin`` from the rest.

    Runs after ``fix_length`` and after any edit that changed a geometry with
    declarations: every segment declared gives ``declared_dimension``, only some give
    ``trace_only`` (spec section 8.2); none left gives the hand-drawn default
    (:func:`initial_metric_origin`) for a manual entity and ``trace_only`` otherwise.
    """
    if operation is not EntityOperation.FIX_LENGTH:
        if entity.geometry == old_geometry:
            return
        attributes = retained_attributes(old_geometry, entity.geometry, entity.attributes)
        if attributes == entity.attributes:
            return
        entity.attributes = attributes
    origin = declared_origin(entity.geometry, entity.attributes)
    if origin is None:
        if entity.source.get("method") == "manual":
            calibration = active_calibration(db, plan)
            origin = initial_metric_origin(plan, calibration, entity.type).value
        else:
            origin = MetricOrigin.TRACE_ONLY.value
    entity.metric_origin = origin


def delete_entity(
    db: Session,
    entity: Entity,
    user: User,
    reason: str | None = None,
    client_ip: str | None = None,
) -> Entity:
    """Soft-delete ``entity`` (``deleted_at``) and append ``entity.deleted`` with ``before``.

    Entities fixed from it can no longer be recomputed: they keep their position and are
    marked (``constraint_error`` ``reference_missing``, ``metric_origin`` ``none``).
    """
    plan, _ = _lock_live(db, entity, user)
    before = entity_state(entity)
    entity.deleted_at = entity.updated_at = utc_now()
    db.flush()
    _record(
        db, plan, entity, user, EventKind.ENTITY_DELETED, client_ip, before=before, reason=reason
    )
    _recompute_dependents(db, plan, entity, user, client_ip)
    recompute_metric_status(db, plan)
    logger.info("User %s deleted entity %s", user.id, entity.id)
    return entity
