"""Case file creation, listing and retrieval, always filtered by membership.

A user sees a case file when they created it or have a ``case_member`` row for it. Every
other case file is treated exactly like a missing one, so callers cannot probe for ids.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import ColumnElement, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sketch2cad.auth.lockout import Clock, utc_now
from sketch2cad.db.models import CaseCodeCounter, CaseFile, CaseMember, IncidentType

CREATOR_PERMISSIONS = ["read", "edit", "deliver"]
"""Permissions granted to the creator of a case file."""

CODE_PREFIX = "EXP"
LIKE_ESCAPE = "\\"


def get_case_clock() -> Clock:
    """FastAPI dependency returning the clock that dates case codes; tests override it."""
    return utc_now


@dataclass(frozen=True)
class NewCaseFile:
    """Validated input for a new case file."""

    incident_type: IncidentType
    incident_date: date
    description: str
    declared_location: str
    organization: str | None = None


def format_case_code(year: int, number: int) -> str:
    """Return the readable code ``EXP-<year>-<NNNN>``."""
    return f"{CODE_PREFIX}-{year:04d}-{number:04d}"


def allocate_case_code(db: Session, year: int) -> str:
    """Take the next number of ``year`` from ``case_code_counter`` and return its code.

    The upsert locks the year's row until the caller's transaction ends, so concurrent
    creations are serialized and never share a number. The caller commits.
    """
    statement = insert(CaseCodeCounter).values(year=year, last_number=1)
    statement = statement.on_conflict_do_update(
        index_elements=[CaseCodeCounter.year],
        set_={"last_number": CaseCodeCounter.last_number + 1},
    ).returning(CaseCodeCounter.last_number)
    number = db.execute(statement).scalar_one()
    return format_case_code(year, number)


def create_case(db: Session, creator_id: uuid.UUID, data: NewCaseFile, now: datetime) -> CaseFile:
    """Insert a case file coded for the UTC year of ``now`` and make the creator a member.

    Runs in the caller's transaction; the caller commits.
    """
    year = now.astimezone(UTC).year
    case_file = CaseFile(
        code=allocate_case_code(db, year),
        incident_type=data.incident_type,
        incident_date=data.incident_date,
        description=data.description,
        declared_location=data.declared_location,
        organization=data.organization,
        created_by=creator_id,
    )
    db.add(case_file)
    db.flush()
    db.add(
        CaseMember(
            case_file_id=case_file.id, user_id=creator_id, permissions=list(CREATOR_PERMISSIONS)
        )
    )
    db.flush()
    db.refresh(case_file)
    return case_file


def visible_to(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Condition matching the case files ``user_id`` created or is a member of."""
    membership = exists().where(
        CaseMember.case_file_id == CaseFile.id, CaseMember.user_id == user_id
    )
    return or_(CaseFile.created_by == user_id, membership)


def escape_like(value: str) -> str:
    """Escape the LIKE wildcards of ``value`` so it matches literally."""
    return (
        value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )


def list_cases(
    db: Session,
    user_id: uuid.UUID,
    q: str | None = None,
    incident_type: IncidentType | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[CaseFile]:
    """Return the case files visible to ``user_id``, newest first.

    ``q`` matches the code, description or declared location case-insensitively as a
    literal substring; ``incident_type`` filters by exact type.
    """
    statement = select(CaseFile).where(visible_to(user_id))
    term = (q or "").strip()
    if term:
        pattern = f"%{escape_like(term)}%"
        statement = statement.where(
            or_(
                CaseFile.code.ilike(pattern, escape=LIKE_ESCAPE),
                CaseFile.description.ilike(pattern, escape=LIKE_ESCAPE),
                CaseFile.declared_location.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    if incident_type is not None:
        statement = statement.where(CaseFile.incident_type == incident_type)
    statement = (
        statement.order_by(CaseFile.created_at.desc(), CaseFile.code.desc())
        .limit(limit)
        .offset(offset)
    )
    return db.scalars(statement).all()


def get_case(db: Session, user_id: uuid.UUID, case_id: uuid.UUID) -> CaseFile | None:
    """Return the case file ``case_id`` if ``user_id`` may see it, else ``None``."""
    statement = select(CaseFile).where(CaseFile.id == case_id, visible_to(user_id))
    return db.scalars(statement).one_or_none()
