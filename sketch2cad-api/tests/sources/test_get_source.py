"""``GET /sources/{id}``: source metadata with the finished ``qc`` result (REQ-02)."""

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import cv2
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
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
from sketch2cad.db.models import Event, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.qc import synthetic
from sketch2cad.worker import tasks_raster
from sketch2cad.worker.app import apply_schema, create_worker_app, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
SOURCES_URL = f"{API_V1_PREFIX}/sources"


@pytest.fixture(scope="module")
def jobs_schema(database_url: str) -> Iterator[str]:
    """Own schema with the app tables and the Procrastinate schema; dropped afterwards."""
    schema = f"test_get_source_{uuid.uuid4().hex}"
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
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]) -> FastAPI:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    return create_app()


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


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


def upload_sharp_png(client: TestClient, case_id: str) -> dict[str, Any]:
    ok, buffer = cv2.imencode(".png", synthetic.sketch(2000))
    assert ok
    data = buffer.tobytes()
    response = client.post(
        f"{CASES_URL}/{case_id}/sources",
        files={"file": ("croquis.png", data, "application/octet-stream")},
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


def test_source_carries_null_qc_until_the_job_runs_then_green(
    client: TestClient, case_id: str, database_url: str, jobs_schema: str
) -> None:
    created = upload_sharp_png(client, case_id)
    url = f"{SOURCES_URL}/{created['id']}"

    queued = client.get(url)
    assert queued.status_code == 200, queued.text
    assert queued.json() == created  # same SourceOut as the 201, qc still null
    assert queued.json()["qc"] is None

    run_worker_once(database_url, jobs_schema)

    response = client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["qc"]["status"] == "green"
    assert {check["name"] for check in body["qc"]["checks"]} >= {
        "short_side",
        "laplacian_variance",
    }
    assert body["qc_job_id"] == created["qc_job_id"]
    assert (body["id"], body["sha256"], body["exif"]) == (
        created["id"],
        created["sha256"],
        created["exif"],
    )


def test_source_of_another_case_is_403_with_access_denied_for_non_member(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
) -> None:
    created = upload_sharp_png(client, case_id)
    outsider = insert_user(factory)
    other = signed_in(app, stack, outsider)

    response = other.get(f"{SOURCES_URL}/{created['id']}")

    assert response.status_code == 403
    assert created["sha256"] not in response.text
    with factory() as db:
        denied = list(db.scalars(select(Event).where(Event.kind == EventKind.ACCESS_DENIED)))
    assert [(e.case_file_id, e.actor_id) for e in denied] == [(uuid.UUID(case_id), outsider.id)]
    assert denied[0].after == {
        "method": "GET",
        "route": f"{SOURCES_URL}/{{source_id}}",
        "permission": "read",
        "status": 403,
    }


@pytest.mark.parametrize("source_id", [str(uuid.uuid4()), "not-a-uuid"])
def test_unknown_or_malformed_source_is_404(client: TestClient, source_id: str) -> None:
    response = client.get(f"{SOURCES_URL}/{source_id}")

    assert response.status_code == 404


def test_anonymous_source_request_is_401(app: FastAPI, stack: ExitStack) -> None:
    anonymous = stack.enter_context(TestClient(app, base_url="https://testserver"))

    assert anonymous.get(f"{SOURCES_URL}/{uuid.uuid4()}").status_code == 401
