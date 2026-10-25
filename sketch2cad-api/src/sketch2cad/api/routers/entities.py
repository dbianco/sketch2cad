"""Entity endpoints: list, create, edit and soft-delete entities of a revision (REQ-05, US-06).

Every mutating route takes ``EDIT_DEPENDENCIES``; the edit lease (task 054) is added there.
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
from sketch2cad.db.models import Entity, Revision, User
from sketch2cad.db.session import get_session
from sketch2cad.entities.schemas import EntitiesCreate, EntityListOut, EntityOut, EntityPatch
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
        "description": "The revision is frozen or the plan is not in EDITING or IN_REVIEW "
        "(``code`` ``V-10`` when validated).",
    },
    422: {"description": "Invalid body, or a layer or symbol not in the active catalog."},
}

ReasonQuery = Annotated[str | None, Query(min_length=1)]


def entity_out(entity: Entity, revision_number: int) -> EntityOut:
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
        }
    )


def _list_out(entities: list[Entity], revision: Revision) -> EntityListOut:
    return EntityListOut(entities=[entity_out(e, revision.number) for e in entities])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _rejected(db: Session, exc: Exception) -> ProblemError:
    db.rollback()
    logger.warning("Rejected entity change: %s", type(exc).__name__)
    if isinstance(exc, EntityGoneError):
        return ProblemError(404, "Not Found", "The entity does not exist.")
    if isinstance(exc, EntityConflictError):
        extensions = {} if exc.code is None else {"code": exc.code}
        return ProblemError(409, HTTPStatus.CONFLICT.phrase, str(exc), extensions=extensions)
    return ProblemError(422, HTTPStatus.UNPROCESSABLE_ENTITY.phrase, str(exc))


def _revision_number(db: Session, entity: Entity) -> int:
    revision = db.get(Revision, entity.revision_id)
    assert revision is not None  # noqa: S101 - guaranteed by the foreign key
    return revision.number


@router.get("/revisions/{revision_id}/entities", responses=READ_RESPONSES)
def get_revision_entities(
    revision: Annotated[Revision, Depends(require_revision_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
) -> EntityListOut:
    """Entities of the revision ordered by id, without soft-deleted ones. Requires ``read``."""
    return _list_out(list_entities(db, revision), revision)


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
    ``metric_origin``. Requires ``edit``; 409 unless the revision is an editable draft.
    """
    try:
        entities = create_entities(db, revision, user, body.entities, _client_ip(request))
    except (EntityConflictError, EntityInputError) as exc:
        raise _rejected(db, exc) from None
    db.commit()
    return _list_out(entities, revision)


@router.patch("/entities/{entity_id}", dependencies=EDIT_DEPENDENCIES, responses=EDIT_RESPONSES)
def patch_entity(
    request: Request,
    body: EntityPatch,
    entity: Annotated[Entity, Depends(require_entity_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> EntityOut:
    """Edit geometry, attributes or layer with an ``operation``; ``source`` is preserved.

    Appends ``entity.updated`` with ``before``, ``after`` (plus ``operation``) and the
    optional ``reason``. Requires ``edit``.
    """
    try:
        update_entity(db, entity, user, body, _client_ip(request))
    except (EntityGoneError, EntityConflictError, EntityInputError) as exc:
        raise _rejected(db, exc) from None
    db.commit()
    return entity_out(entity, _revision_number(db, entity))


@router.delete("/entities/{entity_id}", dependencies=EDIT_DEPENDENCIES, responses=EDIT_RESPONSES)
def remove_entity(
    request: Request,
    entity: Annotated[Entity, Depends(require_entity_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    reason: ReasonQuery = None,
) -> EntityOut:
    """Soft-delete the entity (sets ``deleted_at``; the row stays) with ``entity.deleted``.

    ``reason`` is optional. Requires ``edit``.
    """
    try:
        delete_entity(db, entity, user, reason, _client_ip(request))
    except (EntityGoneError, EntityConflictError) as exc:
        raise _rejected(db, exc) from None
    db.commit()
    return entity_out(entity, _revision_number(db, entity))
