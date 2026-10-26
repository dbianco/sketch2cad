"""Set or change the local frame of a plan (spec sections 8.1, 11.1, 14.1; REQ-04, US-05).

The frame lives in ``local_frame`` of the plan's active calibration. Entity coordinates
drawn before the first frame is set are in :data:`~sketch2cad.geometry.frame.DEFAULT_FRAME`
(paper origin, world axes). When the origin or the +X axis changes, one rigid transform
moves every metric field of every live entity of the draft revision (``coords_m``,
``anchor_m``, ``p1_m``, ``p2_m``, and ``rotation_deg`` of texts and symbols) in the
caller's transaction, and a single event records it: ``local_frame.set`` the first time,
``local_frame.changed`` afterwards. Frozen revisions are never touched. A North-only change
rewrites no entity and writes ``local_frame.changed`` with ``transform`` null; a request
equal to the stored frame writes nothing.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.auth.lockout import utc_now
from sketch2cad.db.models import Calibration, Entity, Plan, User
from sketch2cad.entities.schemas import Geometry
from sketch2cad.entities.service import EntityConflictError, ensure_editable
from sketch2cad.geometry.frame import (
    DEFAULT_FRAME,
    LocalFrame,
    RigidTransform,
    rigid_between,
    world_frame,
)
from sketch2cad.plans.service import (
    active_calibration,
    current_revision,
    recompute_metric_status,
)

logger = logging.getLogger(__name__)

PROVISIONAL_PAPER_SCALE = 100
"""Paper scale of plans without a declared one (``schematic_sketch``, spec section 10)."""

POINT_FIELDS = ("anchor_m", "p1_m", "p2_m")
_GEOMETRY: TypeAdapter[Geometry] = TypeAdapter(Geometry)


class LocalFrameConflictError(Exception):
    """The frame cannot be set now: no active calibration or the plan is not editable (409).

    ``code`` is the PRD validation rule (``V-10``, ``V-12``) or ``lease_required`` when
    one applies; ``extensions`` are extra problem details members (the lease ``holder``).
    """

    def __init__(
        self, message: str, code: str | None = None, extensions: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extensions = dict(extensions or {})


@dataclass(frozen=True)
class FrameUpdate:
    """Outcome of :func:`set_local_frame`; ``event_kind`` is ``None`` when nothing changed."""

    calibration: Calibration
    event_kind: EventKind | None
    transform: RigidTransform | None
    transformed_entities: int


def transform_geometry(geometry: dict[str, Any], transform: RigidTransform) -> dict[str, Any]:
    """``geometry`` moved by ``transform``; a dimension's measured value is recomputed."""
    moved = dict(geometry)
    if "coords_m" in moved:
        moved["coords_m"] = [list(p) for p in transform.apply_points(moved["coords_m"])]
    for field in POINT_FIELDS:
        if field in moved:
            moved[field] = list(transform.apply_point(moved[field]))
    if "rotation_deg" in moved:
        moved["rotation_deg"] = transform.apply_angle(float(moved["rotation_deg"]))
    return _GEOMETRY.validate_python(moved).model_dump(mode="json")


def _paper_scale(calibration: Calibration) -> int:
    return calibration.paper_scale or PROVISIONAL_PAPER_SCALE


def _transform_entities(db: Session, revision_id: uuid.UUID, transform: RigidTransform) -> int:
    query = select(Entity).where(Entity.revision_id == revision_id, Entity.deleted_at.is_(None))
    entities = list(db.scalars(query.order_by(Entity.id).with_for_update()))
    now = utc_now()
    for entity in entities:
        entity.geometry = transform_geometry(entity.geometry, transform)
        entity.updated_at = now
    db.flush()
    return len(entities)


def set_local_frame(
    db: Session, plan: Plan, user: User, frame: LocalFrame, client_ip: str | None = None
) -> FrameUpdate:
    """Store ``frame`` on the active calibration and move the draft entities with it.

    Locks the plan row. Raises :class:`LocalFrameConflictError` before writing anything
    when the plan has no active calibration or its current revision is not editable by
    ``user`` (same rule as entity edits, edit lease included). Runs in the caller's
    transaction; the caller commits.
    """
    db.refresh(plan, with_for_update=True)
    calibration = active_calibration(db, plan)
    if calibration is None:
        raise LocalFrameConflictError("Accept a calibration before setting the local frame.")
    revision = current_revision(db, plan)
    if revision is None:  # pragma: no cover - every plan is created with revision 1
        raise LocalFrameConflictError("The plan has no current revision.")
    try:
        ensure_editable(db, revision, user)
    except EntityConflictError as exc:
        raise LocalFrameConflictError(str(exc), exc.code, exc.extensions) from None
    stored = calibration.local_frame
    previous = None if stored is None else LocalFrame.from_json(stored)
    if previous == frame:
        return FrameUpdate(calibration, None, None, 0)
    old = previous or DEFAULT_FRAME
    transform: RigidTransform | None = None
    count = 0
    if not old.same_placement(frame):
        scale = _paper_scale(calibration)
        transform = rigid_between(world_frame(old, scale), world_frame(frame, scale))
        count = _transform_entities(db, revision.id, transform)
    calibration.local_frame = frame.to_json()
    recompute_metric_status(db, plan)
    kind = EventKind.LOCAL_FRAME_SET if previous is None else EventKind.LOCAL_FRAME_CHANGED
    append_event(
        db,
        case_file_id=plan.case_file_id,
        kind=kind,
        actor_id=user.id,
        actor_role=user.role.value,
        plan_id=plan.id,
        revision_id=revision.id,
        entity_id=calibration.id,
        before=None if previous is None else {"local_frame": previous.to_json()},
        after={
            "local_frame": frame.to_json(),
            "transform": None if transform is None else transform.to_json(),
            "paper_scale": _paper_scale(calibration),
            "transformed_entities": count,
        },
        client_ip=client_ip,
    )
    logger.info(
        "User %s wrote %s on plan %s (%d entities transformed)", user.id, kind, plan.id, count
    )
    return FrameUpdate(calibration, kind, transform, count)
