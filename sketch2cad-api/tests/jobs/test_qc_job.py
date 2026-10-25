"""The ``qc`` worker job and ``GET /jobs/{id}`` (REQ-02, REQ-15)."""

import hashlib
import io
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

import cv2
import psycopg
import pypdfium2  # pyright: ignore[reportMissingTypeStubs]
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db import session as db_session
from sketch2cad.db.base import Base
from sketch2cad.db.models import Event, JobMeta, JobStatus, Source, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.jobs import service
from sketch2cad.jobs.service import QC_TASK_NAME, run_qc_job
from sketch2cad.qc import QcThresholds, synthetic
from sketch2cad.storage import FilesystemStorage
from sketch2cad.worker import tasks_qc, tasks_raster
from sketch2cad.worker.app import apply_schema, create_worker_app, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
JOBS_URL = f"{API_V1_PREFIX}/jobs"


@pytest.fixture(scope="module")
def jobs_schema(database_url: str) -> Iterator[str]:
    """Own schema with the app tables and the Procrastinate schema; dropped afterwards."""
    schema = f"test_jobs_{uuid.uuid4().hex}"
    admin = to_conninfo(database_url)
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield schema
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture(scope="module")
def jobs_engine(database_url: str, jobs_schema: str) -> Iterator[Engine]:
    engine = create_db_engine(
        database_url, connect_args={"options": f"-csearch_path={jobs_schema}"}
    )
    try:
        Base.metadata.create_all(engine)
        apply_schema(to_conninfo(database_url, options=f"-csearch_path={jobs_schema}"))
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def factory(
    jobs_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    session_factory = create_session_factory(jobs_engine)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(tasks_raster, "_session_factory", lambda: session_factory)
    yield session_factory
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with session_factory() as db:
        db.execute(sql_text(f"TRUNCATE {names}, procrastinate_jobs CASCADE"))
        db.commit()


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    return tmp_path / "storage"


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


@pytest.fixture
def app(storage_root: Path, factory: sessionmaker[Session]) -> FastAPI:
    return create_app()


def insert_user(factory: sessionmaker[Session]) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
        )
        db.add(user)
        db.commit()
        return user


def signed_in(app: FastAPI, stack: ExitStack, user: User) -> TestClient:
    client = stack.enter_context(TestClient(app, base_url="https://testserver"))
    response = client.post(
        f"{API_V1_PREFIX}/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, factory: sessionmaker[Session]) -> TestClient:
    return signed_in(app, stack, insert_user(factory))


@pytest.fixture
def case_id(client: TestClient) -> str:
    body = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def encoded(extension: str) -> bytes:
    ok, buffer = cv2.imencode(extension, synthetic.sketch(2000))
    assert ok
    return buffer.tobytes()


def blank_pdf() -> bytes:
    document = pypdfium2.PdfDocument.new()
    document.new_page(595, 842)  # pyright: ignore[reportUnknownMemberType]
    buffer = io.BytesIO()
    document.save(buffer)  # pyright: ignore[reportUnknownMemberType]
    document.close()
    return buffer.getvalue()


def upload(client: TestClient, case_id: str, data: bytes, filename: str) -> dict[str, Any]:
    response = client.post(
        f"{CASES_URL}/{case_id}/sources",
        files={"file": (filename, data, "application/octet-stream")},
        data={"client_sha256": hashlib.sha256(data).hexdigest()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def run_worker_once(database_url: str, jobs_schema: str) -> None:
    """Run the real worker in-process until the queue is empty."""
    conninfo = to_conninfo(database_url, options=f"-csearch_path={jobs_schema}")
    create_worker_app(conninfo).run_worker(
        wait=False, install_signal_handlers=False, listen_notify=False
    )


def load_source(factory: sessionmaker[Session], source_id: str) -> Source:
    with factory() as db:
        source = db.get(Source, uuid.UUID(source_id))
        assert source is not None
        return source


def load_job(factory: sessionmaker[Session], job_id: str) -> JobMeta:
    with factory() as db:
        job = db.get(JobMeta, uuid.UUID(job_id))
        assert job is not None
        return job


def test_sharp_image_job_succeeds_with_green_qc(
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
) -> None:
    body = upload(client, case_id, encoded(".png"), "croquis.png")
    job_url = f"{JOBS_URL}/{body['qc_job_id']}"
    queued = client.get(job_url)
    assert queued.status_code == 200, queued.text
    assert queued.json() == {
        "id": body["qc_job_id"],
        "kind": "qc",
        "status": "queued",
        "source_id": body["id"],
        "error": None,
        "result_ref": None,
    }

    run_worker_once(database_url, jobs_schema)

    response = client.get(job_url)
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["error"]) == ("succeeded", None)
    qc = load_source(factory, body["id"]).qc
    assert qc is not None and qc["status"] == "green"
    assert {check["name"] for check in qc["checks"]} >= {"short_side", "laplacian_variance"}
    job = load_job(factory, body["qc_job_id"])
    assert job.started_at is not None and job.finished_at is not None


def test_corrupt_jpeg_job_fails_with_cause_and_action_and_red_qc(
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
) -> None:
    data = encoded(".jpg")
    body = upload(client, case_id, data[: len(data) // 2], "foto.jpg")  # truncated scan data

    run_worker_once(database_url, jobs_schema)

    response = client.get(f"{JOBS_URL}/{body['qc_job_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    error = response.json()["error"]
    assert error == service.UNREADABLE_IMAGE.to_dict()
    assert error["cause"].strip() and error["action"].strip()
    qc = load_source(factory, body["id"]).qc
    assert qc is not None and qc["status"] == "red"
    assert [check["name"] for check in qc["checks"]] == ["input"]


def test_pdf_job_rasterizes_then_evaluates_the_raster(
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
) -> None:
    body = upload(client, case_id, blank_pdf(), "plano.pdf")

    run_worker_once(database_url, jobs_schema)

    assert load_job(factory, body["qc_job_id"]).status == JobStatus.SUCCEEDED.value
    source = load_source(factory, body["id"])
    assert source.raster_key is not None
    assert source.width_px is not None and source.width_px > 2400  # 300 dpi A4 page
    qc = source.qc
    assert qc is not None and qc["status"] == "red"  # a blank page has no sharpness
    short_side = next(check for check in qc["checks"] if check["name"] == "short_side")
    assert short_side["value"] == source.width_px


def test_job_of_another_case_is_403_with_access_denied_for_non_member(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
) -> None:
    body = upload(client, case_id, encoded(".png"), "croquis.png")
    outsider = insert_user(factory)
    other = signed_in(app, stack, outsider)

    response = other.get(f"{JOBS_URL}/{body['qc_job_id']}")

    assert response.status_code == 403
    assert "qc" not in response.text and body["id"] not in response.text
    with factory() as db:
        denied = list(db.scalars(select(Event).where(Event.kind == EventKind.ACCESS_DENIED)))
    assert [(e.case_file_id, e.actor_id) for e in denied] == [(uuid.UUID(case_id), outsider.id)]
    assert denied[0].after == {
        "method": "GET",
        "route": f"{JOBS_URL}/{{job_id}}",
        "permission": "read",
        "status": 403,
    }


@pytest.mark.parametrize("job_id", [str(uuid.uuid4()), "not-a-uuid"])
def test_unknown_or_malformed_job_is_404(client: TestClient, job_id: str) -> None:
    response = client.get(f"{JOBS_URL}/{job_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "The job does not exist."


def test_anonymous_job_request_is_401(app: FastAPI, stack: ExitStack) -> None:
    anonymous = stack.enter_context(TestClient(app, base_url="https://testserver"))

    assert anonymous.get(f"{JOBS_URL}/{uuid.uuid4()}").status_code == 401


def test_retries_are_idempotent(
    client: TestClient, case_id: str, factory: sessionmaker[Session], storage_root: Path
) -> None:
    body = upload(client, case_id, encoded(".png"), "croquis.png")
    storage = FilesystemStorage(str(storage_root))
    job_id = uuid.UUID(body["qc_job_id"])

    assert run_qc_job(factory, storage, job_id) == "succeeded"
    first = load_job(factory, body["qc_job_id"])
    assert run_qc_job(factory, storage, job_id) == "succeeded"  # finished: left untouched
    assert load_job(factory, body["qc_job_id"]).finished_at == first.finished_at

    with factory() as db, db.begin():  # a worker died while the job was running
        db.execute(sql_text("UPDATE job_meta SET status = 'running', finished_at = NULL"))
    assert run_qc_job(factory, storage, job_id) == "succeeded"
    qc = load_source(factory, body["id"]).qc
    assert qc is not None and qc["status"] == "green"


def test_missing_stored_object_fails_without_green(
    client: TestClient, case_id: str, factory: sessionmaker[Session], tmp_path: Path
) -> None:
    body = upload(client, case_id, encoded(".png"), "croquis.png")

    status = run_qc_job(
        factory, FilesystemStorage(str(tmp_path / "empty")), uuid.UUID(body["qc_job_id"])
    )

    assert status == "failed"
    assert load_job(factory, body["qc_job_id"]).error == service.MISSING_OBJECT.to_dict()
    qc = load_source(factory, body["id"]).qc
    assert qc is not None and qc["status"] == "red"


def test_unexpected_error_marks_the_job_failed_and_reraises(
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = upload(client, case_id, encoded(".png"), "croquis.png")

    def explode(*_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "evaluate", explode)
    with pytest.raises(RuntimeError):
        run_qc_job(factory, FilesystemStorage(str(storage_root)), uuid.UUID(body["qc_job_id"]))

    job = load_job(factory, body["qc_job_id"])
    assert (job.status, job.error) == ("failed", service.INTERNAL_ERROR.to_dict())
    assert load_source(factory, body["id"]).qc is None


def test_unknown_job_raises(factory: sessionmaker[Session], tmp_path: Path) -> None:
    with pytest.raises(service.JobNotFoundError):
        run_qc_job(factory, FilesystemStorage(str(tmp_path)), uuid.uuid4())


def test_worker_app_registers_the_qc_task() -> None:
    app = create_worker_app("dbname=unused")
    tasks = cast(dict[str, Any], app.tasks)  # pyright: ignore[reportUnknownMemberType]

    assert tasks[QC_TASK_NAME].func is tasks_qc.qc


def test_thresholds_come_from_the_environment_without_api_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("SESSION_SECRET", "SIGNING_KEY_PATH", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    assert tasks_qc.qc_thresholds_from_environment() == QcThresholds()

    monkeypatch.setenv("QC_MIN_SHORT_SIDE_AMBER", "1200")
    monkeypatch.setenv("QC_MIN_CONTRAST_AMBER", "45.5")
    thresholds = tasks_qc.qc_thresholds_from_environment()
    assert (thresholds.min_short_side_amber, thresholds.min_contrast_amber) == (1200, 45.5)

    monkeypatch.setenv("QC_MIN_SHORT_SIDE_RED", "0")
    with pytest.raises(ValidationError):
        tasks_qc.qc_thresholds_from_environment()
