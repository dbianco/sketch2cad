"""Case file endpoints: create, list and retrieve, filtered by membership."""

import logging
import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator
from sqlalchemy.orm import Session

from sketch2cad.api.deps import get_current_user, require_csrf
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.auth.lockout import Clock
from sketch2cad.cases.service import (
    NewCaseFile,
    create_case,
    get_case,
    get_case_clock,
    list_cases,
)
from sketch2cad.db.models import CaseFile, IncidentType, User
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


def _out(case_file: CaseFile) -> CaseFileOut:
    return CaseFileOut.model_validate(case_file)


def _not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The case file does not exist.")


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
    case_id: str,
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CaseFileOut:
    """Return a case file; 404 when it is missing, malformed or not visible to the user."""
    try:
        parsed = uuid.UUID(case_id)
    except ValueError:
        raise _not_found() from None
    case_file = get_case(db, user.id, parsed)
    if case_file is None:
        raise _not_found()
    return _out(case_file)
