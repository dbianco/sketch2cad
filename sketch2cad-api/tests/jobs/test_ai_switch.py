"""The ``ai_assist.enabled`` switch: AI job kinds, the suggestion chain, runtime override,
``config.changed`` and ``GET /config/features`` (REQ-17, REQ-18, REQ-19; H5-7)."""

import hashlib
import io
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from psycopg import sql
from sqlalchemy import Engine, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db import session as db_session
from sketch2cad.db.base import Base
from sketch2cad.db.models import ConfigOverride, Event, JobMeta, JobStatus, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.worker import tasks_raster
from sketch2cad.worker.app import apply_schema, create_worker_app, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
SWITCH = "ai_assist.enabled"
SWITCH_URL = f"{API_V1_PREFIX}/config/params/{SWITCH}"
FEATURES_URL = f"{API_V1_PREFIX}/config/features"
AI_KINDS = ("suggest_lines", "ocr_crop")
OCR_BOX = [100, 100, 300, 200]


def sketch_png() -> bytes:
    """A 150 x 100 mm sheet at 254 dpi with one line and one square."""
    image = np.full((1000, 1500), 255, np.uint8)
    cv2.line(image, (200, 300), (1200, 300), 0, 5)
    square = np.array([(300, 600), (700, 600), (700, 900), (300, 900)], np.int32)
    cv2.polylines(image, [square], True, 0, 5)
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG", dpi=(254, 254))
    return buffer.getvalue()


@pytest.fixture(scope="module")
def jobs_schema(database_url: str) -> Iterator[str]:
    schema = f"test_ai_switch_{uuid.uuid4().hex}"
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
    jobs_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[sessionmaker[Session]]:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.delenv("AI_ASSIST_ENABLED", raising=False)
    session_factory = create_session_factory(jobs_engine)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(tasks_raster, "_session_factory", lambda: session_factory)
    yield session_factory
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with session_factory() as db:
        db.execute(sql_text(f"TRUNCATE {names}, procrastinate_jobs CASCADE"))
        db.commit()


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


def login(stack: ExitStack, factory: sessionmaker[Session], role: UserRole) -> TestClient:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=role,
        )
        db.add(user)
        db.commit()
        username = user.username
    client = stack.enter_context(TestClient(create_app(), base_url="https://testserver"))
    response = client.post(
        f"{API_V1_PREFIX}/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def client(stack: ExitStack, factory: sessionmaker[Session]) -> TestClient:
    return login(stack, factory, UserRole.TECHNICIAN)


@pytest.fixture
def admin_client(stack: ExitStack, factory: sessionmaker[Session]) -> TestClient:
    return login(stack, factory, UserRole.ADMIN)


def make_plan(client: TestClient) -> tuple[str, str]:
    """A plan on :func:`sketch_png` with the edit lease; returns (plan id, source id)."""
    case = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=case)
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]
    data = sketch_png()
    response = client.post(
        f"{CASES_URL}/{case_id}/sources",
        files={"file": ("croquis.png", data, "application/octet-stream")},
        data={"client_sha256": hashlib.sha256(data).hexdigest()},
    )
    assert response.status_code == 201, response.text
    source_id = response.json()["id"]
    plan = {"title": "Plano", "primary_source_id": source_id, "source_mode": "schematic_sketch"}
    response = client.post(f"{CASES_URL}/{case_id}/plans", json=plan)
    assert response.status_code == 201, response.text
    plan_id = response.json()["id"]
    assert client.post(f"{PLANS_URL}/{plan_id}/lease").status_code == 200
    return plan_id, source_id


def accept(client: TestClient, plan_id: str, method: str = "scan_dpi") -> None:
    body = {"method": method, "as_metric": False}
    response = client.post(f"{PLANS_URL}/{plan_id}/calibration/accept", json=body)
    assert response.status_code == 200, response.text


def post_job(client: TestClient, plan_id: str, source_id: str, kind: str) -> Any:
    params: dict[str, Any] = {}
    if kind == "ocr_crop":
        params = {"source_id": source_id, "box_px": OCR_BOX}
    return client.post(f"{PLANS_URL}/{plan_id}/jobs", json={"kind": kind, "params": params})


def switch(admin_client: TestClient, enabled: bool) -> Any:
    response = admin_client.put(SWITCH_URL, json={"value": enabled, "scope": "global"})
    assert response.status_code == 200, response.text
    return response.json()


def run_worker_once(database_url: str, jobs_schema: str) -> None:
    conninfo = to_conninfo(database_url, options=f"-csearch_path={jobs_schema}")
    create_worker_app(conninfo).run_worker(
        wait=False, install_signal_handlers=False, listen_notify=False
    )


def jobs(factory: sessionmaker[Session], kind: str) -> list[JobMeta]:
    with factory() as db:
        return list(db.scalars(select(JobMeta).where(JobMeta.kind == kind)))


def events(factory: sessionmaker[Session], kind: str) -> list[Event]:
    with factory() as db:
        return list(db.scalars(select(Event).where(Event.kind == kind).order_by(Event.id)))


def features(client: TestClient) -> Any:
    response = client.get(FEATURES_URL)
    assert response.status_code == 200, response.text
    return response.json()


# --- Switched off -----------------------------------------------------------------------


@pytest.mark.parametrize("kind", AI_KINDS)
def test_with_the_environment_switch_off_both_ai_job_kinds_give_404(
    client: TestClient,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    monkeypatch.setenv("AI_ASSIST_ENABLED", "false")
    plan_id, source_id = make_plan(client)

    response = post_job(client, plan_id, source_id, kind)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "AI assist is disabled."
    assert jobs(factory, kind) == []
    assert features(client) == {"ai_assist": {"enabled": False}}


@pytest.mark.parametrize("kind", AI_KINDS)
def test_with_the_override_off_both_ai_job_kinds_give_404(
    client: TestClient, admin_client: TestClient, factory: sessionmaker[Session], kind: str
) -> None:
    plan_id, source_id = make_plan(client)
    switch(admin_client, False)

    assert post_job(client, plan_id, source_id, kind).status_code == 404
    assert jobs(factory, kind) == []


@pytest.mark.parametrize("method", ["scan_dpi", "none"])
def test_with_the_switch_off_calibration_and_rectify_enqueue_no_suggestions(
    client: TestClient,
    admin_client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
    method: str,
) -> None:
    plan_id, _ = make_plan(client)
    switch(admin_client, False)

    accept(client, plan_id, method)  # ``none`` would queue at once
    run_worker_once(database_url, jobs_schema)  # ``scan_dpi``: rectify runs, its hook too

    if method == "scan_dpi":
        [rectify] = jobs(factory, "rectify")
        assert rectify.status == JobStatus.SUCCEEDED.value
    assert jobs(factory, "suggest_lines") == []
    assert events(factory, "suggestions.added") == []


def test_a_job_queued_before_the_switch_goes_off_inserts_nothing(
    client: TestClient,
    admin_client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
) -> None:
    plan_id, _ = make_plan(client)
    accept(client, plan_id, "none")  # queued now, with the switch on
    switch(admin_client, False)

    run_worker_once(database_url, jobs_schema)

    [job] = jobs(factory, "suggest_lines")
    assert job.status == JobStatus.SUCCEEDED.value
    assert job.params["result"] == {"count": 0, "skipped": "ai_assist_disabled"}
    assert events(factory, "suggestions.added") == []


# --- Switched on --------------------------------------------------------------------------


def test_switching_back_on_at_runtime_relaunches_suggestions_and_allows_ocr(
    client: TestClient,
    admin_client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
) -> None:
    plan_id, source_id = make_plan(client)
    switch(admin_client, False)
    accept(client, plan_id)
    run_worker_once(database_url, jobs_schema)
    assert jobs(factory, "suggest_lines") == []

    response = admin_client.delete(SWITCH_URL, params={"scope": "global"})
    assert response.status_code == 200, response.text
    assert features(client) == {"ai_assist": {"enabled": True}}  # environment default

    response = post_job(client, plan_id, source_id, "suggest_lines")
    assert response.status_code == 202, response.text
    run_worker_once(database_url, jobs_schema)
    [added] = events(factory, "suggestions.added")
    assert added.after is not None and added.after["count"] == 2
    response = post_job(client, plan_id, source_id, "ocr_crop")
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "ocr_crop"


def test_a_global_override_on_beats_the_environment_off(
    client: TestClient,
    admin_client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_ASSIST_ENABLED", "false")
    plan_id, _ = make_plan(client)
    assert features(client) == {"ai_assist": {"enabled": False}}

    switch(admin_client, True)
    assert features(client) == {"ai_assist": {"enabled": True}}
    accept(client, plan_id)
    run_worker_once(database_url, jobs_schema)

    [job] = jobs(factory, "suggest_lines")
    assert job.status == JobStatus.SUCCEEDED.value and job.params["result"]["count"] == 2
    assert len(events(factory, "suggestions.added")) == 1


# --- config.changed, features endpoint and permissions -------------------------------------


def test_each_change_of_the_switch_writes_config_changed_in_the_global_chain(
    admin_client: TestClient, factory: sessionmaker[Session]
) -> None:
    body = switch(admin_client, False)
    assert body["scopes"] == ["global"] and body["default"] is True
    assert [o["value"] for o in body["overrides"]] == [False]
    switch(admin_client, True)
    response = admin_client.delete(SWITCH_URL, params={"scope": "global"})
    assert response.status_code == 200, response.text

    changes = events(factory, "config.changed")
    assert [e.case_file_id for e in changes] == [None, None, None]
    target = {"key": SWITCH, "scope": "global", "incident_type": None}
    assert [(e.before, e.after) for e in changes] == [
        (None, {**target, "value": False}),
        ({**target, "value": False}, {**target, "value": True}),
        ({**target, "value": True}, None),
    ]
    assert all(e.actor_role == "admin" for e in changes)


def test_only_an_admin_with_csrf_changes_the_switch_and_only_globally(
    client: TestClient, admin_client: TestClient, factory: sessionmaker[Session]
) -> None:
    body = {"value": False, "scope": "global"}
    assert client.put(SWITCH_URL, json=body).status_code == 403
    assert client.delete(SWITCH_URL, params={"scope": "global"}).status_code == 403
    by_type = {"value": False, "scope": "incident_type", "incident_type": "traffic_collision"}
    assert admin_client.put(SWITCH_URL, json=by_type).status_code == 422
    assert admin_client.put(SWITCH_URL, json={"value": 0, "scope": "global"}).status_code == 422
    token = admin_client.headers.pop(CSRF_HEADER)
    assert admin_client.put(SWITCH_URL, json=body).status_code == 403
    admin_client.headers[CSRF_HEADER] = token

    with factory() as db:
        assert list(db.scalars(select(ConfigOverride))) == []
    assert events(factory, "config.changed") == []
    assert features(client) == {"ai_assist": {"enabled": True}}


def test_the_features_endpoint_needs_a_session_and_reflects_the_switch(
    client: TestClient, admin_client: TestClient, stack: ExitStack
) -> None:
    anonymous = stack.enter_context(TestClient(create_app(), base_url="https://testserver"))
    assert anonymous.get(FEATURES_URL).status_code == 401

    assert features(client) == {"ai_assist": {"enabled": True}}
    switch(admin_client, False)
    assert features(client) == {"ai_assist": {"enabled": False}}
    assert features(admin_client) == {"ai_assist": {"enabled": False}}
