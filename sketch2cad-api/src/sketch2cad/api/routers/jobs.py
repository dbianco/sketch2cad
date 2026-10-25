"""Job status endpoint, polled by the client every 2 s (REQ-02, REQ-15)."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api.deps import (
    get_audit_session_factory,
    get_current_user,
    require_source_permission,
)
from sketch2cad.api.errors import ProblemError
from sketch2cad.api.routers import api_v1_router
from sketch2cad.db.models import JobMeta, User
from sketch2cad.db.session import get_session

router = api_v1_router(tags=["jobs"])

JOB_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user lacks ``read`` on the job's case file (access.denied)."},
    404: {"description": "The job does not exist."},
}


class JobErrorOut(BaseModel):
    """Why the job failed and what to do, in Spanish (REQ-15)."""

    cause: str
    action: str


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    source_id: uuid.UUID | None
    error: JobErrorOut | None
    result_ref: str | None


def _job_not_found() -> ProblemError:
    return ProblemError(404, "Not Found", "The job does not exist.")


def _readable_job(
    job_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    audit_factory: Annotated[sessionmaker[Session], Depends(get_audit_session_factory)],
) -> JobMeta:
    """The ``{job_id}`` job if the user holds ``read`` on its source's case file.

    Unknown or malformed ids, and jobs without a source, give 404. Denials are 403 with
    ``access.denied``, exactly as for the source itself.
    """
    try:
        job = db.get(JobMeta, uuid.UUID(job_id))
    except ValueError:
        raise _job_not_found() from None
    if job is None or job.source_id is None:
        raise _job_not_found()
    check = require_source_permission("read")
    check(str(job.source_id), request, db, user, audit_factory)
    return job


@router.get("/jobs/{job_id}", responses=JOB_RESPONSES)
def get_job(job: Annotated[JobMeta, Depends(_readable_job)]) -> JobOut:
    """Status of a job; ``error {cause, action}`` is present only when it failed.

    Requires ``read`` on the case file of the job's source.
    """
    error = JobErrorOut.model_validate(job.error) if job.error is not None else None
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,  # pyright: ignore[reportArgumentType]
        source_id=job.source_id,
        error=error,
        result_ref=job.result_ref,
    )
