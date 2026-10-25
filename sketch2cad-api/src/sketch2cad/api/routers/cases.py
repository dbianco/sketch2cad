"""Case file endpoints: create, list, retrieve and add members, filtered by membership."""

import logging
import uuid
from datetime import date, datetime
from http import HTTPStatus
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy.orm import Session

from sketch2cad.api.deps import (
    get_current_user,
    require_case_manager,
    require_case_permission,
    require_csrf,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.audit.chain import EventKind, append_event
from sketch2cad.auth.lockout import Clock
from sketch2cad.cases.permissions import (
    CasePermission,
    MemberExistsError,
    UnknownUserError,
    add_member,
)
from sketch2cad.cases.service import (
    NewCaseFile,
    create_case,
    get_case_clock,
    list_cases,
)
from sketch2cad.db.models import CaseFile, CaseMember, IncidentType, User
from sketch2cad.db.session import get_session

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["case-files"])

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200

RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CaseFileCreate(BaseModel):
    incident_type: IncidentType
    incident_date: date
    description: RequiredText
    declared_location: RequiredText
    organization: Annotated[str, StringConstraints(strip_whitespace=True)] | None = None

    @field_validator("organization")
    @classmethod
    def blank_organization_is_none(cls, value: str | None) -> str | None:
        return value or None


class CaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    incident_type: IncidentType
    incident_date: date
    description: str
    declared_location: str
    organization: str | None
    status: str
    created_by: uuid.UUID
    created_at: datetime


class CaseFileList(BaseModel):
    items: list[CaseFileOut]


class CaseMemberCreate(BaseModel):
    user_id: uuid.UUID
    permissions: Annotated[list[CasePermission], Field(min_length=1)]


class CaseMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_file_id: uuid.UUID
    user_id: uuid.UUID
    permissions: list[CasePermission]


def _out(case_file: CaseFile) -> CaseFileOut:
    return CaseFileOut.model_validate(case_file)


def _created_payload(case_file: CaseFile) -> dict[str, str | None]:
    return {
        "code": case_file.code,
        "incident_type": case_file.incident_type.value,
        "incident_date": case_file.incident_date.isoformat(),
        "declared_location": case_file.declared_location,
        "organization": case_file.organization,
    }


@router.post("/case-files", status_code=201, dependencies=[Depends(require_csrf)])
def create_case_file(
    body: CaseFileCreate,
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    clock: Annotated[Clock, Depends(get_case_clock)],
) -> CaseFileOut:
    """Create a case file with the next ``EXP-<year>-<NNNN>`` code; the creator becomes member."""
    data = NewCaseFile(
        incident_type=body.incident_type,
        incident_date=body.incident_date,
        description=body.description,
        declared_location=body.declared_location,
        organization=body.organization,
    )
    case_file = create_case(db, user.id, data, clock())
    append_event(
        db,
        case_file_id=case_file.id,
        kind=EventKind.CASE_FILE_CREATED,
        actor_id=user.id,
        actor_role=user.role.value,
        after=_created_payload(case_file),
    )
    db.commit()
    logger.info("User %s created case file %s (%s)", user.id, case_file.id, case_file.code)
    return _out(case_file)


@router.get("/case-files")
def list_case_files(
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    q: str | None = None,
    incident_type: IncidentType | None = None,
    limit: Annotated[int, Query(ge=1)] = DEFAULT_LIST_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CaseFileList:
    """List the case files the user created or is a member of, newest first.

    ``limit`` above the maximum is capped rather than rejected.
    """
    rows = list_cases(
        db,
        user.id,
        q=q,
        incident_type=incident_type,
        limit=min(limit, MAX_LIST_LIMIT),
        offset=offset,
    )
    return CaseFileList(items=[_out(row) for row in rows])


@router.get("/case-files/{case_id}")
def get_case_file(
    case_file: Annotated[CaseFile, Depends(require_case_permission("read"))],
) -> CaseFileOut:
    """Return a case file; 404 when it is missing or malformed.

    A user without ``read`` (member or not) gets 403; every denial on an existing case file
    is logged as ``access.denied``.
    """
    return _out(case_file)


@router.post("/case-files/{case_id}/members", status_code=201, dependencies=[Depends(require_csrf)])
def add_case_member(
    body: CaseMemberCreate,
    case_file: Annotated[CaseFile, Depends(require_case_manager)],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CaseMemberOut:
    """Add a member with explicit permissions; only an ``admin`` or the creator may do it.

    Anyone else gets 403, logged as ``access.denied``; an unknown
    ``user_id`` gives 422 and an existing member 409.
    """
    try:
        member: CaseMember = add_member(db, case_file, body.user_id, body.permissions, user)
    except UnknownUserError:
        db.rollback()
        raise ProblemError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            HTTPStatus.UNPROCESSABLE_ENTITY.phrase,
            "The user does not exist.",
        ) from None
    except MemberExistsError:
        db.rollback()
        raise ProblemError(
            409, "Conflict", "The user is already a member of the case file."
        ) from None
    db.commit()
    logger.info("User %s added member %s to case file %s", user.id, body.user_id, case_file.id)
    return CaseMemberOut.model_validate(member)
