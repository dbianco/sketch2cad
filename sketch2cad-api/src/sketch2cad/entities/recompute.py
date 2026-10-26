"""Recompute the entities fixed by a constraint when a reference changes (spec 11.3, REQ-06).

An entity placed by ``triangulate`` or ``baseline_offset`` stores its ``constraint``; when
one of its references moves (or is deleted), every entity that depends on it directly or
indirectly is placed again from its stored constraint and the current positions of its
references. Dependents are visited breadth-first and recomputed so that an entity comes
after the dependents it refers to; a cycle already stored in the database cannot hang the
walk (each entity is recomputed at most once).

Rules, chosen so that a recompute is deterministic and idempotent:

- A triangulation keeps the side of A→B it was applied on (``params.side``); the stored
  ``solution`` (``alt`` is relative to the position of P when it was applied) is not
  re-evaluated. Without a stored side (tangent at apply time, or older constraints) the
  candidate nearest to the current position wins, ties to the left.
- When the entity cannot be placed (circles that no longer cross, ``V-06``; a reference
  deleted or no longer a point; base points that coincide) it keeps its position and its
  constraint, ``metric_origin`` becomes ``none`` and ``attributes.constraint_error`` names
  the cause. It is placed again, and the flag removed, on the next successful recompute.
- When it can be placed but a reference is not metric (``V-07``) it follows the reference,
  but ``metric_origin`` is ``none`` and ``constraint_error`` is ``V-07``.

The caller appends one ``entity.recomputed`` event per changed entity in its transaction.
A rigid change of the local frame moves references and dependents together and keeps every
constraint satisfied, so it does not recompute (:mod:`sketch2cad.plans.local_frame`).
"""

import math
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.db.models import Entity, MetricOrigin
from sketch2cad.entities.constraints import (
    TriangulationConstraint,
    parse_constraint,
    point_position,
    with_position,
)
from sketch2cad.geometry.constraints import dependents_breadth_first
from sketch2cad.geometry.homography import Point
from sketch2cad.geometry.metric_status import METRIC_ORIGINS
from sketch2cad.geometry.metrics import (
    DegenerateGeometryError,
    baseline_offset_point,
    circle_intersections,
)

CONSTRAINT_ERROR: Final = "constraint_error"
"""Server-managed attribute naming why the last recompute could not keep the entity metric."""

REFERENCE_MISSING: Final = "reference_missing"
DEGENERATE_BASELINE: Final = "degenerate_baseline"
INVALID_CONSTRAINT: Final = "invalid_constraint"

CONSTRAINT_KIND_ORIGINS: Final = {
    "triangulation": MetricOrigin.TRIANGULATION.value,
    "baseline_offset": MetricOrigin.BASELINE_OFFSET.value,
}


@dataclass(frozen=True)
class Recomputed:
    """A dependent whose state changed; ``error`` is the ``constraint_error`` or ``None``."""

    entity: Entity
    before: dict[str, Any]
    error: str | None


def constraint_graph(db: Session, revision_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    """``entity -> refs`` of the live constrained entities of the revision."""
    query = select(Entity.id, Entity.constraint).where(
        Entity.revision_id == revision_id,
        Entity.deleted_at.is_(None),
        Entity.constraint.is_not(None),
    )
    graph: dict[uuid.UUID, list[uuid.UUID]] = {}
    for entity_id, constraint in db.execute(query):
        refs: list[Any] = constraint.get("refs", []) if isinstance(constraint, dict) else []
        graph[entity_id] = [uuid.UUID(str(ref)) for ref in refs]
    return graph


def dependency_order[K](graph: Mapping[K, Iterable[K]], affected: list[K]) -> list[K]:
    """``affected`` (nearest first) reordered so each entity follows the ones it refers to.

    Among entities that are ready, the breadth-first order is kept. Entities left on a
    stored cycle are taken in breadth-first order, each once.
    """
    members = set(affected)
    placed: set[K] = set()
    pending = list(affected)
    order: list[K] = []
    while pending:
        ready = [
            node
            for node in pending
            if all(ref not in members or ref in placed for ref in graph.get(node, ()))
        ]
        for node in ready or pending[:1]:
            placed.add(node)
            order.append(node)
        pending = [node for node in pending if node not in placed]
    return order


def recompute_dependents(
    db: Session, changed: Entity, state: Callable[[Entity], dict[str, Any]]
) -> list[Recomputed]:
    """Place again every entity depending on ``changed``; return the ones that changed.

    ``state`` snapshots an entity before it is touched (the event ``before``). Locks the
    dependent rows and flushes. Runs in the caller's transaction.
    """
    graph = constraint_graph(db, changed.revision_id)
    affected = dependents_breadth_first(graph, changed.id)
    if not affected:
        return []
    query = select(Entity).where(Entity.id.in_(affected)).with_for_update()
    rows = {row.id: row for row in db.scalars(query)}
    results: list[Recomputed] = []
    for entity_id in dependency_order(graph, affected):
        entity = rows[entity_id]
        before = state(entity)
        error = _recompute(db, entity)
        if state(entity) != before:
            results.append(Recomputed(entity, before, error))
    db.flush()
    return results


def _recompute(db: Session, entity: Entity) -> str | None:
    """Place ``entity`` from its constraint; returns the ``constraint_error`` it now has."""
    try:
        constraint = parse_constraint(entity.constraint)
    except ValidationError:
        return _mark(entity, INVALID_CONSTRAINT)
    refs: list[Entity] = []
    for ref_id in constraint.refs:
        ref = db.get(Entity, ref_id)
        if ref is None or ref.deleted_at is not None or ref.revision_id != entity.revision_id:
            return _mark(entity, REFERENCE_MISSING)
        refs.append(ref)
    a, b = (point_position(ref.geometry) for ref in refs)
    current = point_position(entity.geometry)
    if a is None or b is None or current is None:
        return _mark(entity, REFERENCE_MISSING)
    if isinstance(constraint, TriangulationConstraint):
        point = _triangulated(constraint, a, b, current)
        if point is None:
            return _mark(entity, "V-06")
    else:
        params = constraint.params
        try:
            point = baseline_offset_point(a, b, params.s_m, params.d_m, params.side)
        except DegenerateGeometryError:
            return _mark(entity, DEGENERATE_BASELINE)
    if point != current:
        entity.geometry = with_position(entity.geometry, point)
    if any(ref.metric_origin not in METRIC_ORIGINS for ref in refs):
        return _mark(entity, "V-07")
    entity.metric_origin = CONSTRAINT_KIND_ORIGINS[constraint.kind]
    if CONSTRAINT_ERROR in entity.attributes:
        entity.attributes = {k: v for k, v in entity.attributes.items() if k != CONSTRAINT_ERROR}
    return None


def _triangulated(
    constraint: TriangulationConstraint, a: Point, b: Point, current: Point
) -> Point | None:
    params = constraint.params
    points = circle_intersections(a, params.d_a_m, b, params.d_b_m).points
    if len(points) < 2:
        return points[0] if points else None
    if params.side is not None:
        return points[0] if params.side == "left" else points[1]
    return points[1] if math.dist(points[1], current) < math.dist(points[0], current) else points[0]


def _mark(entity: Entity, error: str) -> str:
    """The entity is not metric until a later recompute succeeds."""
    entity.metric_origin = MetricOrigin.NONE.value
    if entity.attributes.get(CONSTRAINT_ERROR) != error:
        entity.attributes = {**entity.attributes, CONSTRAINT_ERROR: error}
    return error
