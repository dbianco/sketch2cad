"""Event log endpoint: the hash-chained events of one case file (REQ-11, REQ-01).

Items carry every stored field of the event, ``prev_hash`` and ``hash`` included, with
``occurred_at`` in the exact text that was hashed, so the concatenated pages re-verify with
:func:`sketch2cad.audit.verify.verify_chain`. Only the chain of the case file is exposed:
the global auth chain (``case_file_id`` null) is never listed here.
"""

import base64
import binascii
import uuid
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sketch2cad.api.deps import require_case_permission
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.audit.canonical import format_timestamp
from sketch2cad.audit.chain import EventKind
from sketch2cad.db.models import CaseFile, Event
from sketch2cad.db.session import get_session

router = api_v1_router(tags=["events"])

PAGE_SIZE = 50
MAX_EVENT_ID = 2**63 - 1  # ``event.id`` is a PostgreSQL bigint

EVENTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``read`` on the case file (access.denied)."},
    404: {"description": "The case file does not exist."},
    422: {"description": "Unknown ``kind``, malformed ``plan_id`` or invalid ``cursor``."},
}


class EventOut(BaseModel):
    """One event exactly as hashed; ``occurred_at`` is UTC ISO 8601 with microseconds."""

    id: int
    case_file_id: uuid.UUID | None
    plan_id: uuid.UUID | None
    revision_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    actor_role: str | None
    kind: str
    entity_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    client_ip: str | None
    occurred_at: str
    prev_hash: str
    hash: str


class EventPage(BaseModel):
    """Up to 50 events in ascending id; ``next_cursor`` is null on the last page."""

    items: list[EventOut]
    next_cursor: str | None


def _invalid_cursor() -> ProblemError:
    return ProblemError(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        HTTPStatus.UNPROCESSABLE_ENTITY.phrase,
        "The cursor is invalid.",
    )


def encode_cursor(last_id: int) -> str:
    """Opaque cursor pointing after the event ``last_id`` (unpadded URL-safe base64)."""
    return base64.urlsafe_b64encode(str(last_id).encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int:
    """The event id a cursor points after; 422 problem when it was not issued by us."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise _invalid_cursor() from None
    if not raw.isdigit() or raw != str(int(raw)) or int(raw) > MAX_EVENT_ID:
        raise _invalid_cursor()
    return int(raw)


def _out(event: Event) -> EventOut:
    return EventOut(
        id=event.id,
        case_file_id=event.case_file_id,
        plan_id=event.plan_id,
        revision_id=event.revision_id,
        actor_id=event.actor_id,
        actor_role=event.actor_role,
        kind=event.kind,
        entity_id=event.entity_id,
        before=event.before,
        after=event.after,
        reason=event.reason,
        client_ip=event.client_ip,
        occurred_at=format_timestamp(event.occurred_at),
        prev_hash=event.prev_hash,
        hash=event.hash,
    )


@router.get("/case-files/{case_id}/events", responses=EVENTS_RESPONSES)
def list_case_events(
    case_file: Annotated[CaseFile, Depends(require_case_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
    plan_id: uuid.UUID | None = None,
    kind: EventKind | None = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> EventPage:
    """Events of the case file in id order, 50 per page, optionally filtered.

    Pass ``next_cursor`` back as ``cursor`` to get the following page. Requires ``read``;
    a denial is a 403 logged as ``access.denied``.
    """
    statement = select(Event).where(Event.case_file_id == case_file.id)
    if plan_id is not None:
        statement = statement.where(Event.plan_id == plan_id)
    if kind is not None:
        statement = statement.where(Event.kind == kind.value)
    if cursor is not None:
        statement = statement.where(Event.id > decode_cursor(cursor))
    rows = list(db.scalars(statement.order_by(Event.id).limit(PAGE_SIZE + 1)).all())
    page = rows[:PAGE_SIZE]
    next_cursor = encode_cursor(page[-1].id) if len(rows) > PAGE_SIZE else None
    return EventPage(items=[_out(event) for event in page], next_cursor=next_cursor)
