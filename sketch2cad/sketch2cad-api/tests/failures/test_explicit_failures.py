"""Every explicit failure path ends in a visible state with ``cause`` and ``action`` (CA-11).

H6-2: an unreadable image, a calibration out of tolerance and a job forced to fail each
show a state whose ``cause`` and ``action`` are non-empty Spanish text, never a green or
``succeeded`` default (REQ-15, US-18). The plumbing (isolated schema with the Procrastinate
tables, signed-in client, uploads, in-process worker) is reused from the ``qc`` job tests
and the calibration helpers from the calibration API tests (fixtures in ``conftest.py``).
"""

import hashlib
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType
from typing import Any

import cv2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from tests.calibration.test_calibration_api import (
    accept,
    calibrations,
    check_point,
    make_plan,
    photo_body,
    propose,
)
from tests.jobs.test_qc_job import (
    CASES_URL,
    JOBS_URL,
    encoded,
    insert_user,
    run_worker_once,
    signed_in,
    upload,
)

import sketch2cad.calibration.rectify as rectify_job
from sketch2cad.api.errors import INTERNAL_ERROR_ACTION, INTERNAL_ERROR_CAUSE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.api.routers import jobs as jobs_router
from sketch2cad.db.models import JobMeta, User
from sketch2cad.jobs import service
from sketch2cad.jobs.service import JobError
from sketch2cad.qc import synthetic
from sketch2cad.qc.checks import laplacian_variance, to_gray
from sketch2cad.storage import FilesystemStorage, Storage
from sketch2cad.worker import tasks_export, tasks_ocr, tasks_suggest

SOURCES_URL = f"{API_V1_PREFIX}/sources"
LEAKED_DETAIL = "internal-detail /srv/objects/croquis.jpg"
"""Text of the forced exceptions: it must never reach the user."""

RunJob = Callable[[sessionmaker[Session], Storage, uuid.UUID], str]


@pytest.fixture
def owner(factory: sessionmaker[Session]) -> User:
    return insert_user(factory)


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    return signed_in(app, stack, owner)


def assert_explained(failure: dict[str, Any]) -> None:
    """``failure`` carries non-empty ``cause`` and ``action`` strings, free of internals."""
    cause, action = failure.get("cause"), failure.get("action")
    assert isinstance(cause, str) and cause.strip(), failure
    assert isinstance(action, str) and action.strip(), failure
    for text in (cause, action):
        assert LEAKED_DETAIL not in text and "Traceback" not in text and "Error:" not in text


def assert_job_failed(client: TestClient, job_id: uuid.UUID, expected: JobError) -> None:
    """``GET /jobs/{id}`` shows ``failed`` with exactly ``expected``, never ``succeeded``."""
    response = client.get(f"{JOBS_URL}/{job_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["result_ref"] is None
    assert body["error"] == expected.to_dict()
    assert_explained(body["error"])
    assert LEAKED_DETAIL not in response.text and "Traceback" not in response.text


def get_source(client: TestClient, source_id: str) -> dict[str, Any]:
    response = client.get(f"{SOURCES_URL}/{source_id}")
    assert response.status_code == 200, response.text
    return response.json()


def truncated_jpeg() -> bytes:
    data = encoded(".jpg")
    return data[: len(data) // 2]  # a valid signature whose scan data stops halfway


def insert_job(
    factory: sessionmaker[Session], kind: str, source_id: str, params: dict[str, Any]
) -> uuid.UUID:
    with factory() as db:
        job = JobMeta(kind=kind, source_id=uuid.UUID(source_id), params=params)
        db.add(job)
        db.commit()
        return job.id


# (a) Unreadable image ------------------------------------------------------------------


def test_blurry_image_with_laplacian_variance_30_is_red_with_cause_and_action(
    client: TestClient, case_id: str, database_url: str, jobs_schema: str
) -> None:
    image = synthetic.with_laplacian_variance(synthetic.sketch(2000), 30.0)
    assert laplacian_variance(to_gray(image)) == pytest.approx(30.0, abs=1.0)
    ok, png = cv2.imencode(".png", image)
    assert ok
    body = upload(client, case_id, png.tobytes(), "croquis.png")

    run_worker_once(database_url, jobs_schema)

    qc = get_source(client, body["id"])["qc"]
    assert qc is not None and qc["status"] == "red"
    sharpness = next(c for c in qc["checks"] if c["name"] == "laplacian_variance")
    assert sharpness["status"] == "red"
    assert_explained(sharpness)
    assert "30" in sharpness["cause"]
    for check in qc["checks"]:
        if check["status"] != "green":
            assert_explained(check)


def test_truncated_image_fails_the_job_and_leaves_a_red_qc_with_cause_and_action(
    client: TestClient, case_id: str, database_url: str, jobs_schema: str
) -> None:
    body = upload(client, case_id, truncated_jpeg(), "foto.jpg")

    run_worker_once(database_url, jobs_schema)

    assert_job_failed(client, uuid.UUID(body["qc_job_id"]), service.UNREADABLE_IMAGE)
    qc = get_source(client, body["id"])["qc"]
    assert qc is not None and qc["status"] == "red"
    assert [check["name"] for check in qc["checks"]] == ["input"]
    assert_explained(qc["checks"][0])


def test_undecodable_upload_is_rejected_with_cause_and_action(
    client: TestClient, case_id: str
) -> None:
    data = b"\xff\xd8\xff\xe0" + b"not really a jpeg" * 64
    response = client.post(
        f"{CASES_URL}/{case_id}/sources",
        files={"file": ("foto.jpg", data, "image/jpeg")},
        data={"client_sha256": hashlib.sha256(data).hexdigest()},
    )

    assert response.status_code == 415, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert_explained(response.json())


# (b) Calibration out of tolerance ------------------------------------------------------


def test_calibration_with_a_1_3_mm_error_is_never_metric_and_explains_why(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    plan_id = make_plan(client, factory, case_id, owner)
    body = photo_body(check_points=[check_point(1.3)])

    proposal = propose(client, plan_id, body)
    response = accept(client, plan_id, body, True)

    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["meets_tolerance"] is False
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["code"] == "V-05"
    assert_explained(problem)
    assert "1,30 mm" in problem["cause"] and "1,00 mm" in problem["cause"]
    assert problem["detail"] == f"{problem['cause']} {problem['action']}"
    assert calibrations(factory, plan_id) == []


def test_metric_calibration_without_method_explains_why(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    plan_id = make_plan(client, factory, case_id, owner)

    response = accept(client, plan_id, {"method": "none"}, True)

    assert response.status_code == 422, response.text
    assert_explained(response.json())


# (c) Jobs forced to fail ---------------------------------------------------------------


def _explode(*_args: object, **_kwargs: object) -> Any:
    raise RuntimeError(LEAKED_DETAIL)


FORCED: list[tuple[str, ModuleType, str, RunJob]] = [
    ("rectify", rectify_job, "_rectify", rectify_job.run_rectify_job),
    ("suggest_lines", tasks_suggest, "_load_inputs", tasks_suggest.run_suggest_job),
    ("ocr_crop", tasks_ocr, "_load_image", tasks_ocr.run_ocr_job),
    ("export", tasks_export, "_run", tasks_export.run_export_job),
]
"""Job kind, module, the step made to raise and the job runner of each worker job."""


@pytest.mark.parametrize(("kind", "module", "target", "run"), FORCED, ids=[f[0] for f in FORCED])
def test_unexpected_job_error_fails_with_a_spanish_cause_and_action(
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    module: ModuleType,
    target: str,
    run: RunJob,
) -> None:
    source = upload(client, case_id, encoded(".png"), "croquis.png")
    job_id = insert_job(factory, kind, source["id"], {})
    monkeypatch.setattr(module, target, _explode)

    with pytest.raises(RuntimeError):
        run(factory, FilesystemStorage(str(storage_root)), job_id)

    assert_job_failed(client, job_id, module.INTERNAL_ERROR)


def test_expected_job_failures_carry_their_own_cause_and_action(
    client: TestClient, case_id: str, factory: sessionmaker[Session], storage_root: Path
) -> None:
    storage = FilesystemStorage(str(storage_root))
    source = upload(client, case_id, truncated_jpeg(), "foto.jpg")["id"]
    missing = {key: str(uuid.uuid4()) for key in ("plan_id", "revision_id", "requested_by")}
    cases: list[tuple[uuid.UUID, RunJob, JobError]] = [
        (
            insert_job(factory, "rectify", source, {}),
            rectify_job.run_rectify_job,
            rectify_job.MISSING_CALIBRATION,
        ),
        (
            insert_job(factory, "suggest_lines", source, {}),
            tasks_suggest.run_suggest_job,
            rectify_job.MISSING_CALIBRATION,
        ),
        (
            insert_job(factory, "ocr_crop", source, {"box_px": [0, 0, 64, 64]}),
            tasks_ocr.run_ocr_job,
            service.UNREADABLE_IMAGE,
        ),
        (
            insert_job(factory, "export", source, {"mode": "draft", **missing}),
            tasks_export.run_export_job,
            tasks_export.MISSING_PLAN,
        ),
    ]

    for job_id, run, expected in cases:
        assert run(factory, storage, job_id) == "failed"
        assert_job_failed(client, job_id, expected)


def test_worker_turns_an_unexpected_exception_into_a_spanish_cause_and_action(
    client: TestClient,
    case_id: str,
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = upload(client, case_id, encoded(".png"), "croquis.png")
    monkeypatch.setattr(service, "evaluate", _explode)

    run_worker_once(database_url, jobs_schema)  # the real worker, in process

    assert_job_failed(client, uuid.UUID(body["qc_job_id"]), service.INTERNAL_ERROR)
    assert get_source(client, body["id"])["qc"] is None  # no default green either


def test_unexpected_api_error_is_a_problem_with_cause_and_action(
    app: FastAPI, client: TestClient, case_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = upload(client, case_id, encoded(".png"), "croquis.png")
    monkeypatch.setattr(jobs_router, "job_out", _explode)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as raw:
        raw.cookies = client.cookies
        response = raw.get(f"{JOBS_URL}/{body['qc_job_id']}")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert (problem["cause"], problem["action"]) == (INTERNAL_ERROR_CAUSE, INTERNAL_ERROR_ACTION)
    assert_explained(problem)
    assert LEAKED_DETAIL not in response.text
