"""``GET /plans/{plan_id}/jobs``: the jobs the editor follows after a calibration.

The plan, schema, signed-in clients and in-process worker come from the AI switch tests.
"""

import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

API_DIR = Path(__file__).resolve().parents[2]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from tests.jobs.test_ai_switch import (  # noqa: E402
    PLANS_URL,
    accept,
    admin_client,
    client,
    factory,
    jobs_engine,
    jobs_schema,
    login,
    make_plan,
    run_worker_once,
    stack,
)

from sketch2cad.api.routers.jobs import PLAN_JOBS_LIMIT  # noqa: E402
from sketch2cad.calibration.rectify import CANVAS_TOO_LARGE  # noqa: E402
from sketch2cad.db.models import Event, JobMeta, JobStatus, UserRole  # noqa: E402

__all__ = ["admin_client", "client", "factory", "jobs_engine", "jobs_schema", "stack"]

T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def add_job(
    factory: sessionmaker[Session],
    plan_id: str | uuid.UUID,
    source_id: str | uuid.UUID,
    kind: str,
    *,
    status: JobStatus = JobStatus.QUEUED,
    minutes: int = 0,
    error: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """Insert a ``job_meta`` row created ``minutes`` after :data:`T0`; return its id."""
    with factory() as db:
        job = JobMeta(
            id=uuid.uuid4(),
            plan_id=uuid.UUID(str(plan_id)),
            source_id=uuid.UUID(str(source_id)),
            kind=kind,
            params=params or {},
            status=status.value,
            error=error,
            created_at=T0 + timedelta(minutes=minutes),
        )
        db.add(job)
        db.commit()
        return str(job.id)


def list_jobs(client: TestClient, plan_id: str, **params: Any) -> list[dict[str, Any]]:
    response = client.get(f"{PLANS_URL}/{plan_id}/jobs", params=params)
    assert response.status_code == 200, response.text
    return response.json()["jobs"]


def test_lists_the_plans_jobs_newest_first_with_the_failure(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    plan_id, source_id = make_plan(client)
    calibration_id = str(uuid.uuid4())
    failed = add_job(
        factory,
        plan_id,
        source_id,
        "rectify",
        status=JobStatus.FAILED,
        minutes=1,
        error=CANVAS_TOO_LARGE.to_dict(),
        params={"calibration_id": calibration_id},
    )
    running = add_job(
        factory, plan_id, source_id, "suggest_lines", status=JobStatus.RUNNING, minutes=2
    )
    oldest = add_job(factory, plan_id, source_id, "export", status=JobStatus.SUCCEEDED)

    jobs = list_jobs(client, plan_id)

    assert [job["id"] for job in jobs] == [running, failed, oldest]
    assert jobs[1] == {
        "id": failed,
        "kind": "rectify",
        "status": "failed",
        "source_id": source_id,
        "error": {
            "cause": "La imagen rectificada sería demasiado grande para generarse.",
            "action": "Revisa las dimensiones conocidas o los puntos de calibración y vuelve "
            "a aceptarla.",
        },
        "result_ref": None,
        "result": None,
        "created_at": "2026-10-27T12:01:00Z",
        "calibration_id": calibration_id,
    }
    assert jobs[0]["status"] == "running" and jobs[0]["error"] is None
    assert jobs[0]["calibration_id"] is None


def test_kind_filters_and_repeats(client: TestClient, factory: sessionmaker[Session]) -> None:
    plan_id, source_id = make_plan(client)
    rectify = add_job(factory, plan_id, source_id, "rectify", minutes=1)
    suggest = add_job(factory, plan_id, source_id, "suggest_lines", minutes=2)
    add_job(factory, plan_id, source_id, "ocr_crop", minutes=3)

    assert [job["id"] for job in list_jobs(client, plan_id, kind="rectify")] == [rectify]
    both = list_jobs(client, plan_id, kind=["rectify", "suggest_lines"])
    assert [job["id"] for job in both] == [suggest, rectify]
    assert list_jobs(client, plan_id, kind="unknown") == []


def test_jobs_of_another_plan_or_source_are_not_listed(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    plan_id, source_id = make_plan(client)
    other_plan_id, other_source_id = make_plan(client)
    own = add_job(factory, plan_id, source_id, "rectify")
    add_job(factory, other_plan_id, other_source_id, "rectify")
    add_job(factory, plan_id, other_source_id, "rectify")  # not the primary source

    assert [job["id"] for job in list_jobs(client, plan_id)] == [own]


def test_at_most_the_limit_of_newest_jobs(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    plan_id, source_id = make_plan(client)
    ids = [add_job(factory, plan_id, source_id, "ocr_crop", minutes=n) for n in range(25)]

    jobs = list_jobs(client, plan_id)

    assert len(jobs) == PLAN_JOBS_LIMIT
    assert [job["id"] for job in jobs] == ids[::-1][:PLAN_JOBS_LIMIT]


def test_an_outsider_gets_403_with_access_denied_and_anonymous_401(
    client: TestClient, factory: sessionmaker[Session], stack: Any
) -> None:
    plan_id, source_id = make_plan(client)
    add_job(factory, plan_id, source_id, "rectify")
    outsider = login(stack, factory, UserRole.TECHNICIAN)

    response = outsider.get(f"{PLANS_URL}/{plan_id}/jobs")

    assert response.status_code == 403, response.text
    assert "jobs" not in response.json()
    with factory() as db:
        denied = db.scalars(select(Event).where(Event.kind == "access.denied")).all()
    assert len(denied) == 1
    anonymous = stack.enter_context(TestClient(outsider.app, base_url="https://testserver"))
    assert anonymous.get(f"{PLANS_URL}/{plan_id}/jobs").status_code == 401


def test_unknown_plan_gives_404(client: TestClient) -> None:
    assert client.get(f"{PLANS_URL}/{uuid.uuid4()}/jobs").status_code == 404


def test_follows_rectify_then_the_chained_suggest_job(
    client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
) -> None:
    plan_id, _ = make_plan(client)
    accept(client, plan_id)
    calibration_id = client.get(f"{PLANS_URL}/{plan_id}").json()["calibration"]["id"]

    [queued] = list_jobs(client, plan_id, kind=["rectify", "suggest_lines"])
    assert (queued["kind"], queued["status"]) == ("rectify", "queued")
    assert queued["calibration_id"] == calibration_id

    run_worker_once(database_url, jobs_schema)

    jobs = list_jobs(client, plan_id, kind=["rectify", "suggest_lines"])
    assert [(job["kind"], job["status"]) for job in jobs] == [
        ("suggest_lines", "succeeded"),
        ("rectify", "succeeded"),
    ]
    assert {job["calibration_id"] for job in jobs} == {calibration_id}


@pytest.mark.parametrize("status", [JobStatus.SUCCEEDED, JobStatus.FAILED])
def test_reading_the_list_changes_nothing(
    client: TestClient, factory: sessionmaker[Session], status: JobStatus
) -> None:
    plan_id, source_id = make_plan(client)
    add_job(factory, plan_id, source_id, "rectify", status=status)
    with factory() as db:
        before = db.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))

    list_jobs(client, plan_id)

    with factory() as db:
        assert db.scalar(select(Event.id).order_by(Event.id.desc()).limit(1)) == before
