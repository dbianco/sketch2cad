"""Evidence register endpoints: list, create and edit the evidence of a case file (REQ-08).

Numbers are assigned by the server, sequentially per case file without gaps or reuse
(:mod:`sketch2cad.evidence.service`); a request can never set or change them. Mutating
routes take ``require_csrf``.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.orm import Session

from sketch2cad.api.deps import (
    get_current_user,
    require_case_permission,
    require_csrf,
    require_evidence_permission,
)
from sketch2cad.api.routers import api_v1_router
from sketch2cad.db.models import CaseFile, Evidence, User
from sketch2cad.db.session import get_session
from sketch2cad.entities.schemas import Attributes, AttributeValue
from sketch2cad.evidence.service import create_evidence, list_evidence, update_evidence

logger = logging.getLogger(__name__)

router = api_v1_router(tags=["evidence"])

_DENIED = "The user lacks the permission on the case file (access.denied)."
CASE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": _DENIED},
    404: {"description": "The case file does not exist."},
}
EVIDENCE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": _DENIED},
    404: {"description": "The evidence does not exist."},
}

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EvidenceCreate(BaseModel):
    """New evidence; ``attributes`` values are JSON primitives, like entity attributes."""

    model_config = ConfigDict(extra="forbid")

    label: Label
    attributes: Attributes = Field(default_factory=dict[str, AttributeValue])


class EvidencePatch(BaseModel):
    """An edit of ``label`` and/or ``attributes`` (replaced whole); ``number`` is immutable."""

    model_config = ConfigDict(extra="forbid")

    label: Label | None = None
    attributes: Attributes | None = None

    @model_validator(mode="after")
    def _changes_something(self) -> "EvidencePatch":
        if self.label is None and self.attributes is None:
            raise ValueError("an evidence edit needs label or attributes")
        return self


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_file_id: uuid.UUID
    number: int
    label: str
    attributes: dict[str, Any]
    created_by: uuid.UUID
    created_at: datetime


class EvidenceListOut(BaseModel):
    """Evidence of a case file ordered by number."""

    items: list[EvidenceOut]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _out(evidence: Evidence) -> EvidenceOut:
    return EvidenceOut.model_validate(evidence)


@router.get("/case-files/{case_id}/evidence", responses=CASE_RESPONSES)
def get_case_evidence(
    case_file: Annotated[CaseFile, Depends(require_case_permission("read"))],
    db: Annotated[Session, Depends(get_session)],
) -> EvidenceListOut:
    """Evidence register of the case file, ordered by number. Requires ``read``."""
    return EvidenceListOut(items=[_out(e) for e in list_evidence(db, case_file)])


@router.post(
    "/case-files/{case_id}/evidence",
    status_code=201,
    dependencies=[Depends(require_csrf)],
    responses=CASE_RESPONSES,
)
def post_case_evidence(
    request: Request,
    body: EvidenceCreate,
    case_file: Annotated[CaseFile, Depends(require_case_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> EvidenceOut:
    """Register evidence with the next number of the case file and ``evidence.created``.

    Requires ``edit``.
    """
    evidence = create_evidence(
        db, case_file, user, body.label, dict(body.attributes), _client_ip(request)
    )
    db.commit()
    return _out(evidence)


@router.patch(
    "/evidence/{evidence_id}",
    dependencies=[Depends(require_csrf)],
    responses=EVIDENCE_RESPONSES,
)
def patch_evidence(
    request: Request,
    body: EvidencePatch,
    evidence: Annotated[Evidence, Depends(require_evidence_permission("edit"))],
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> EvidenceOut:
    """Change the label and/or attributes with ``evidence.updated``; the number stays.

    Requires ``edit`` on the evidence's case file.
    """
    attributes = None if body.attributes is None else dict(body.attributes)
    update_evidence(db, evidence, user, body.label, attributes, _client_ip(request))
    db.commit()
    return _out(evidence)
