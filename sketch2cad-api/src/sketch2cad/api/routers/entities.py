"""Entity endpoints: list, create, edit, soft-delete and bulk-review entities of a revision.

REQ-05, US-06; the bulk review of suggestions covers REQ-17 and US-12.

Every mutating route takes ``EDIT_DEPENDENCIES`` (CSRF); the edit lease is checked in the
service gate :func:`~sketch2cad.entities.service.ensure_editable`, under the plan row lock.
"""

import logging
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from sketch2cad.api.deps import (
    get_current_user,
    require_csrf,
    require_entity_permission,
    require_revision_permission,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.db.models import Entity, Plan, ReviewStatus, Revision, User
from sketch2cad.db.session import get_session
from sketch2cad.entities.bulk_review import BulkReviewError, bulk_review
from sketch2cad.entities.dimensions import DimensionTolerances, dimension_discrepancy
from sketch2cad.entities.schemas import (
    BulkReviewIn,
    BulkReviewOut,
    EntitiesCreate,
    EntityListOut,
    EntityOut,
    EntityPatch,
    EntityPatchOut,
)
from sketch2cad.entities.service import (
    EntityConflictError,
    EntityGoneError,
    EntityInputError,
    create_entities,
    delete_entity,
    list_entities,
    update_entity,
)

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["entities"])

EDIT_DEPENDENCIES = [Depends(require_csrf)]

_DENIED = "The user lacks the permission on the plan's case file (access.denied)."
READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": _DENIED},
    404: {"description": "The revision does not exist."},
}
EDIT_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": _DENIED},
    404: {"description": "The revision or entity does not exist, or the entity is deleted."},
    409: {
        "description": "The revision is frozen, the plan is not in EDITING or IN_REVIEW "
        "(``code`` ``V-10`` when validated), or the user does not hold the plan's edit "
        "lease (``code`` ``V-12`` with ``holder``, or ``lease_required``).",
    },
    422: {
        "description": "Invalid body, a layer or symbol not in the active catalog, an "
        "``evidence_id`` that is not evidence of the plan's case file (or set on an entity "
        "that is not a point or symbol), or a "
        "``fix_length``, ``triangulate`` or ``baseline_offset`` that cannot be applied: "
        "``code`` ``V-04`` for a distance <= 0, ``V-06`` when the two distances do not "
        "cross, ``V-07`` when a reference is not metric or depends on the entity. "
        "Nothing changes.",
    },
}

BULK_REVIEW_RESPONSES: dict[int | str, dict[str, Any]] = {
    **EDIT_RESPONSES,
    404: {"description": "The revision does not exist."},
    422: {
        "description": "Invalid body (empty, repeated or more than 2000 ids, a status other "
        "than accepted or rejected), or ``code`` ``bulk_review_invalid`` when some id cannot "
        "be reviewed: ``invalid_ids`` lists them and ``reasons`` maps each to "
        "``not_in_revision``, ``deleted``, ``not_a_suggestion`` or ``invalid_transition``. "
        "Nothing changes.",
    },
}

ReasonQuery = Annotated[str | None, Query(min_length=1)]


def revision_tolerances(db: Session, revision: Revision) -> DimensionTolerances:
    """``dimension.discrepancy_tolerance_m`` for the incident type of the revision's case."""
    plan = db.get(Plan, revision.plan_id)
    assert plan is not None  # noqa: S101 - guaranteed by the foreign key
    return DimensionTolerances.for_case(db, plan.case_file_id)


def entity_out(entity: Entity, revision_number: int, tolerances: DimensionTolerances) -> EntityOut:
    return EntityOut.model_validate(
        {
            "id": entity.id,
            "revision_id": entity.revision_id,
            "revision_number": revision_number,
            "type": entity.type,
            "layer": entity.layer,
            "geometry": entity.geometry,
            "image_region": entity.image_region,
            "attributes": entity.attributes,
            "evidence_id": entity.evidence_id,
            "constraint": entity.constraint,
            "source": entity.source,
            "confidence": entity.confidence,
            "model_version": entity.model_version,
            "review_status": entity.review_status,
            "metric_origin": entity.metric_origin,
            "created_by": entity.created_by,
            "updated_at": entity.updated_at,
            "deleted_at": entity.deleted_at,
            "discrepancy": dimension_discrepancy(entity.type, entity.geometry, tolerances),
        }
    )


def _list_out(
    entities: list[Entity], revision: Revision, tolerances: DimensionTolerances
) -> EntityListOut:
    return EntityListOut(entities=[entity_out(e, revision.number, tolerances) for e in entities])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _rejected(db: Session, exc: Exception) -> ProblemError:
    db.rollback()
    logger.warning("Rejected entity change: %s", type(exc).__name__)
    if isinstance(exc, EntityGoneError):
        return ProblemError(404, "Not Found", "The entity does not exist.")
    if isinstance(exc, BulkReviewError):
        extensions = {"code": exc.code, "invalid_ids": list(exc.reasons), "reasons": exc.reasons}
        return ProblemError(
            422, HTTPStatus.UNPROCESSABLE_ENTITY.phrase, str(exc), extensions=extensions
        )
    if isinstance(exc, EntityConflictError):
        extensions = {**exc.extensions, **({} if exc.code is None else {"code": exc.code})}
        return ProblemError(409, HTTPStatus.CONFLICT.phrase, str(exc), extensions=extensions)
    code = getattr(exc, "code", None)
    extensions = {} if code is None else {"code": code}
    return ProblemError(
        422, HTTPStatus.UNPROCESSABLE_ENTITY.phrase, str(exc), extensions=extensions
    )


def _revision(db: Session, entity: Entity) -> Revision:
    revision = db.get(Revision, entity.revision_id)
    assert revision is not None  # noqa: S101 - guaranteed by the foreign key
    return revision


def _entity_out(db: Session, entity: Entity) -> EntityOut:
    revision = _revision(db, entity)
    return entity_out(entity, revision.number, revision_tolerances(db, revision))


@router.get("/revisions/{revision_id}/entities", responses=READ_RESPONSES)
def get_revision_entities(
    revision: Annotated[Revision, Depends(require_revision_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
) -> EntityListOut:
    """Entities of the revision ordered by id, without soft-deleted ones. Requires ``read``."""
    tolerances = revision_tolerances(db, revision)
    return _list_out(list_entities(db, revision), revision, tolerances)


@router.post(
    "/revisions/{revision_id}/entities",
    status_code=201,
    dependencies=EDIT_DEPENDENCIES,
    responses=EDIT_RESPONSES,
)
def create_revision_entities(
    request: Request,
    body: EntitiesCreate,
    revision: Annotated[Revision, Depends(require_revision_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> EntityListOut:
    """Create one or more hand-drawn entities, in request order, with ``entity.created`` each.

    The server sets ``source`` (``manual``), ``review_status`` (``accepted``) and
    ``metric_origin``. A dimension with ``ocr_job_id`` (a succeeded ``ocr_crop`` job of the
    plan) gets ``source.method = ocr``, ``accepted`` when ``declared_value_m`` equals the
    OCR value else ``corrected``, and also ``entity.reviewed``. Requires ``edit``; 409
    unless the revision is an editable draft.
    """
    try:
        entities = create_entities(db, revision, user, body.entities, _client_ip(request))
    except (EntityConflictError, EntityInputError) as exc:
        raise _rejected(db, exc) from None
    db.commit()
    return _list_out(entities, revision, revision_tolerances(db, revision))


@router.post(
    "/revisions/{revision_id}/entities:bulk-review",
    dependencies=EDIT_DEPENDENCIES,
    responses=BULK_REVIEW_RESPONSES,
)
def bulk_review_entities(
    request: Request,
    body: BulkReviewIn,
    revision: Annotated[Revision, Depends(require_revision_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> BulkReviewOut:
    """Accept or reject suggestions of the revision in one transaction, all or nothing.

    One ``entity.reviewed`` per changed entity; accepting promotes ``metric_origin``
    (``scaled_drawing`` in a ``scaled_drawing`` plan calibrated as metric, else
    ``trace_only``), rejecting also soft-deletes. Ids already in the target state are
    left unchanged without an event. Requires ``edit`` and the edit lease.
    """
    try:
        result = bulk_review(
            db, revision, user, body.ids, ReviewStatus(body.review_status), _client_ip(request)
        )
    except (EntityConflictError, BulkReviewError) as exc:
        raise _rejected(db, exc) from None
    db.commit()
    return BulkReviewOut.model_validate(
        {
            "review_status": body.review_status,
            "changed": len(result.changed),
            "unchanged": len(result.unchanged),
            "changed_ids": result.changed,
            "unchanged_ids": result.unchanged,
            "metric_status": result.metric_status,
        }
    )


@router.patch("/entities/{entity_id}", dependencies=EDIT_DEPENDENCIES, responses=EDIT_RESPONSES)
def patch_entity(
    request: Request,
    body: EntityPatch,
    entity: Annotated[Entity, Depends(require_entity_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> EntityPatchOut:
    """Edit geometry, attributes or layer with an ``operation``; ``source`` is preserved.

    Changing a pending suggestion marks it ``corrected`` (CA-05) and promotes its
    ``metric_origin`` like an acceptance.

    Appends ``entity.updated`` with ``before``, ``after`` (plus ``operation``) and the
    optional ``reason``. ``triangulate`` and ``baseline_offset`` store the ``constraint``
    and set ``metric_origin``; ``triangulate`` also returns ``candidates``. Requires
    ``edit``.
    """
    try:
        update = update_entity(db, entity, user, body, _client_ip(request))
    except (EntityGoneError, EntityConflictError, EntityInputError) as exc:
        raise _rejected(db, exc) from None
    db.commit()
    out = _entity_out(db, entity)
    candidates = None if update.candidates is None else [list(c) for c in update.candidates]
    return EntityPatchOut.model_validate(
        {**out.model_dump(), "candidates": candidates, "chosen_candidate": update.chosen}
    )


@router.delete("/entities/{entity_id}", dependencies=EDIT_DEPENDENCIES, responses=EDIT_RESPONSES)
def remove_entity(
    request: Request,
    entity: Annotated[Entity, Depends(require_entity_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    reason: ReasonQuery = None,
) -> EntityOut:
    """Soft-delete the entity (sets ``deleted_at``; the row stays) with ``entity.deleted``.

    Deleting a pending suggestion rejects it (``review_status`` ``rejected``). ``reason`` is
    optional. Requires ``edit``.
    """
    try:
        delete_entity(db, entity, user, reason, _client_ip(request))
    except (EntityGoneError, EntityConflictError) as exc:
        raise _rejected(db, exc) from None
    db.commit()
    return _entity_out(db, entity)
