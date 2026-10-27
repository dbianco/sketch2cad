"""Export jobs, artifact download and the final-export guards (REQ-12..14, US-15, US-16, V-11).

The real worker runs in-process (``run_worker(wait=False)``) against an own schema holding
the app tables and the Procrastinate schema; storage and the signing key live in
``tmp_path``. The final package is checked with the standalone verifier as a subprocess.
"""

import hashlib
import io
import json
import logging
import subprocess
import sys
import uuid
import zipfile
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
from sketch2cad.audit.verify import verify_chain
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db import session as db_session
from sketch2cad.db.base import Base
from sketch2cad.db.models import (
    Artifact,
    Calibration,
    CaseMember,
    Event,
    JobMeta,
    MetricStatus,
    Plan,
    PlanStatus,
    Revision,
    Source,
    User,
    UserRole,
)
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.exports.service import V11_MESSAGE
from sketch2cad.jobs.service import MISSING_OBJECT
from sketch2cad.package.signing import generate_signing_key
from sketch2cad.plans.transitions import TransitionConflictError, mark_exported
from sketch2cad.qc import synthetic
from sketch2cad.revisions.snapshot import SnapshotError
from sketch2cad.storage import FilesystemStorage
from sketch2cad.storage.base import key_for_hash
from sketch2cad.worker import tasks_export, tasks_raster
from sketch2cad.worker.app import apply_schema, create_worker_app, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
API = API_V1_PREFIX
POLYLINE = {"kind": "polyline", "coords_m": [[0.0, 0.0], [4.2, 0.0], [4.2, 3.0]], "closed": False}
VERIFIER = Path(__file__).resolve().parents[2] / "verifier" / "sketch2cad_verify.py"
FINAL_KINDS = {"zip", "dxf", "pdf_plan", "pdf_audit", "manifest"}


@pytest.fixture(scope="module")
def jobs_schema(database_url: str) -> Iterator[str]:
    schema = f"test_exports_{uuid.uuid4().hex}"
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
    key_path = tmp_path / "keys" / "signing-key.pem"
    generate_signing_key(key_path)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", str(key_path))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    return create_app()


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


def insert_user(factory: sessionmaker[Session], role: UserRole) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name=f"{role.value.title()} {uuid.uuid4().hex[:4]}",
            password_hash=hash_password(PASSWORD),
            role=role,
        )
        db.add(user)
        db.commit()
        return user


def signed_in(app: FastAPI, stack: ExitStack, user: User) -> TestClient:
    client = stack.enter_context(TestClient(app, base_url="https://testserver"))
    response = client.post(
        f"{API}/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def owner(factory: sessionmaker[Session]) -> User:
    return insert_user(factory, UserRole.TECHNICIAN)


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    """The technician who created the case file (holds every case permission)."""
    return signed_in(app, stack, owner)


@pytest.fixture
def plan_id(client: TestClient, factory: sessionmaker[Session], owner: User) -> str:
    """A plan in ``EDITING`` from an uploaded PNG, with an active calibration."""
    body = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(f"{API}/case-files", json=body)
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]
    ok, buffer = cv2.imencode(".png", synthetic.sketch(600))
    assert ok
    data = buffer.tobytes()
    response = client.post(
        f"{API}/case-files/{case_id}/sources",
        files={"file": ("croquis.png", data, "application/octet-stream")},
        data={"client_sha256": hashlib.sha256(data).hexdigest()},
    )
    assert response.status_code == 201, response.text
    source_id = response.json()["id"]
    response = client.post(
        f"{API}/case-files/{case_id}/plans", json={"title": "Plano", "primary_source_id": source_id}
    )
    assert response.status_code == 201, response.text
    plan_id = response.json()["id"]
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        plan.status = PlanStatus.EDITING
        plan.metric_status = MetricStatus.APPROXIMATE
        db.add(Calibration(plan_id=plan.id, method="scan_dpi", accepted_by=owner.id))
        db.commit()
    assert client.post(f"{API}/plans/{plan_id}/lease").status_code == 200
    revision_id = client.get(f"{API}/plans/{plan_id}").json()["current_revision_id"]
    entity = {"type": "polyline", "layer": "L_STRUCTURE", "geometry": POLYLINE}
    response = client.post(f"{API}/revisions/{revision_id}/entities", json={"entities": [entity]})
    assert response.status_code == 201, response.text
    return plan_id


def member(
    app: FastAPI,
    stack: ExitStack,
    factory: sessionmaker[Session],
    plan_id: str,
    role: UserRole,
    permissions: list[str],
) -> tuple[User, TestClient]:
    user = insert_user(factory, role)
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        db.add(CaseMember(case_file_id=plan.case_file_id, user_id=user.id, permissions=permissions))
        db.commit()
    return user, signed_in(app, stack, user)


@pytest.fixture
def reviewer(
    app: FastAPI, stack: ExitStack, factory: sessionmaker[Session], plan_id: str
) -> TestClient:
    return member(app, stack, factory, plan_id, UserRole.REVIEWER, ["read", "edit", "deliver"])[1]


def validate(client: TestClient, reviewer: TestClient, plan_id: str) -> None:
    """Drive the plan to ``VALIDATED`` through the transition endpoint."""
    for actor, to in [(client, "REVIEW_PENDING"), (reviewer, "IN_REVIEW"), (reviewer, "VALIDATED")]:
        response = actor.post(f"{API}/plans/{plan_id}/transition", json={"to": to})
        assert response.status_code == 200, response.text


def export(client: TestClient, plan_id: str, **body: Any) -> Any:
    return client.post(f"{API}/plans/{plan_id}/exports", json=body)


def run_worker_once(database_url: str, jobs_schema: str) -> None:
    conninfo = to_conninfo(database_url, options=f"-csearch_path={jobs_schema}")
    create_worker_app(conninfo).run_worker(
        wait=False, install_signal_handlers=False, listen_notify=False
    )


def stored_plan(factory: sessionmaker[Session], plan_id: str) -> Plan:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        return plan


def set_status(factory: sessionmaker[Session], plan_id: str, status: PlanStatus) -> None:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        plan.status = status
        db.commit()


def events(factory: sessionmaker[Session], kind: EventKind) -> list[Event]:
    with factory() as db:
        return list(db.scalars(select(Event).where(Event.kind == kind.value).order_by(Event.id)))


def artifacts(factory: sessionmaker[Session], plan_id: str) -> list[Artifact]:
    with factory() as db:
        query = select(Artifact).where(Artifact.plan_id == uuid.UUID(plan_id))
        return list(db.scalars(query.order_by(Artifact.kind)))


def denied(factory: sessionmaker[Session]) -> list[Any]:
    return [(e.after or {}).get("permission") for e in events(factory, EventKind.ACCESS_DENIED)]


def test_final_on_an_editing_plan_is_409_with_the_v11_message(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    response = export(client, plan_id, mode="final")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == V11_MESSAGE
    assert response.json()["detail"] == "La exportación final requiere un plano validado."
    assert response.json()["code"] == "V-11"
    assert events(factory, EventKind.EXPORT_REQUESTED) == []
    with factory() as db:
        assert db.scalars(select(JobMeta).where(JobMeta.kind == "export")).all() == []


def test_final_without_deliver_is_403_with_access_denied(
    app: FastAPI, stack: ExitStack, factory: sessionmaker[Session], plan_id: str
) -> None:
    _, editor = member(app, stack, factory, plan_id, UserRole.REVIEWER, ["read", "edit"])

    response = export(editor, plan_id, mode="final")

    assert response.status_code == 403, response.text
    assert denied(factory) == ["deliver"]
    assert events(factory, EventKind.EXPORT_REQUESTED) == []


def test_draft_needs_edit_and_a_plan_in_editing_or_later(
    app: FastAPI, stack: ExitStack, factory: sessionmaker[Session], plan_id: str
) -> None:
    _, reader = member(app, stack, factory, plan_id, UserRole.TECHNICIAN, ["read"])
    assert export(reader, plan_id, mode="draft").status_code == 403
    assert denied(factory) == ["edit"]

    set_status(factory, plan_id, PlanStatus.CALIBRATION_PENDING)
    _, editor = member(app, stack, factory, plan_id, UserRole.TECHNICIAN, ["read", "edit"])
    response = export(editor, plan_id, mode="draft")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "draft_not_allowed"


@pytest.mark.parametrize("status", [PlanStatus.EDITING, PlanStatus.IN_REVIEW])
def test_draft_export_stores_hashed_artifacts_and_keeps_the_plan_status(
    app: FastAPI,
    stack: ExitStack,
    factory: sessionmaker[Session],
    plan_id: str,
    status: PlanStatus,
    database_url: str,
    jobs_schema: str,
    tmp_path: Path,
) -> None:
    set_status(factory, plan_id, status)
    _, editor = member(app, stack, factory, plan_id, UserRole.TECHNICIAN, ["read", "edit"])

    response = export(editor, plan_id, mode="draft", paper="A3", scale=100)
    assert response.status_code == 202, response.text
    job = response.json()
    assert (job["kind"], job["status"], job["result_ref"]) == ("export", "queued", None)
    assert export(editor, plan_id, mode="draft", paper="A3", scale=100).json()["id"] == job["id"]

    run_worker_once(database_url, jobs_schema)

    polled = editor.get(f"{API}/jobs/{job['id']}").json()
    assert (polled["status"], polled["error"]) == ("succeeded", None), polled
    assert stored_plan(factory, plan_id).status is status
    rows = artifacts(factory, plan_id)
    assert [(a.kind, a.is_draft) for a in rows] == [("dxf", True), ("pdf_plan", True)]
    assert polled["result_ref"] == str(next(a.id for a in rows if a.kind == "pdf_plan"))
    storage = FilesystemStorage(tmp_path / "storage")
    for row in rows:
        data = storage.get(row.storage_key)
        assert hashlib.sha256(data).hexdigest() == row.sha256
        assert len(data) == row.byte_size
    assert b"DRAFT" in storage.get(rows[0].storage_key)
    created = events(factory, EventKind.ARTIFACT_CREATED)
    assert sorted((e.after or {})["kind"] for e in created) == ["dxf", "pdf_plan"]
    assert len(events(factory, EventKind.EXPORT_REQUESTED)) == 1
    assert events(factory, EventKind.PLAN_TRANSITION) == []

    listed = editor.get(f"{API}/plans/{plan_id}/artifacts")
    assert listed.status_code == 200, listed.text
    assert {item["kind"] for item in listed.json()} == {"dxf", "pdf_plan"}
    detail = editor.get(f"{API}/artifacts/{rows[1].id}").json()
    assert detail["is_draft"] is True and detail["manifest"] is None
    assert detail["filename"].endswith("_r1_plano_borrador.pdf")


def test_final_export_packages_signs_and_moves_the_plan_to_exported(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    reviewer: TestClient,
    factory: sessionmaker[Session],
    plan_id: str,
    database_url: str,
    jobs_schema: str,
    tmp_path: Path,
) -> None:
    validate(client, reviewer, plan_id)
    response = export(reviewer, plan_id, mode="final")
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert export(reviewer, plan_id, mode="final", paper="A3").json()["id"] == job_id

    run_worker_once(database_url, jobs_schema)

    polled = reviewer.get(f"{API}/jobs/{job_id}").json()
    assert (polled["status"], polled["error"]) == ("succeeded", None), polled
    plan = stored_plan(factory, plan_id)
    assert plan.status is PlanStatus.EXPORTED
    last = events(factory, EventKind.PLAN_TRANSITION)[-1]
    assert (last.before, last.after) == ({"status": "VALIDATED"}, {"status": "EXPORTED"})
    rows = {a.kind: a for a in artifacts(factory, plan_id)}
    assert set(rows) == FINAL_KINDS
    assert all(
        not a.is_draft and a.revision_id == plan.validated_revision_id for a in rows.values()
    )
    assert polled["result_ref"] == str(rows["zip"].id)
    assert len(events(factory, EventKind.ARTIFACT_CREATED)) == len(FINAL_KINDS)

    package_url = f"{API}/artifacts/{rows['zip'].id}"
    detail = reviewer.get(package_url)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert (body["kind"], body["is_draft"], body["sha256"]) == ("zip", False, rows["zip"].sha256)
    manifest = body["manifest"]
    assert manifest["plan_id"] == plan_id
    with factory() as db:
        revision = db.get(Revision, plan.validated_revision_id)
        assert revision is not None
    assert manifest["revision"]["snapshot_sha256"] == revision.snapshot_sha256
    assert {a["kind"] for a in manifest["artifacts"]} >= {"dxf", "pdf_plan", "pdf_audit"}
    assert manifest["signature"]["scheme"] == "ed25519-jcs-v1"
    assert events(factory, EventKind.ARTIFACT_DOWNLOADED) == []

    download = reviewer.get(package_url, params={"confirm": "true"})
    assert download.status_code == 200, download.text
    digest = hashlib.sha256(download.content).hexdigest()
    assert digest == download.headers["X-Content-SHA256"] == rows["zip"].sha256
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert download.headers["Content-Disposition"].startswith("attachment;")
    downloaded = events(factory, EventKind.ARTIFACT_DOWNLOADED)
    assert len(downloaded) == 1 and downloaded[0].entity_id == rows["zip"].id

    package = tmp_path / "package.zip"
    package.write_bytes(download.content)
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(VERIFIER), str(package)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr

    again = export(reviewer, plan_id, mode="final")
    assert (again.status_code, again.json()["code"]) == (409, "V-11")

    _, reader = member(app, stack, factory, plan_id, UserRole.TECHNICIAN, ["read"])
    assert reader.get(package_url).status_code == 200
    assert reader.get(package_url, params={"confirm": "true"}).status_code == 403
    outsider = signed_in(app, stack, insert_user(factory, UserRole.REVIEWER))
    assert outsider.get(package_url).status_code == 403
    assert denied(factory) == ["deliver", "read"]
    assert len(events(factory, EventKind.ARTIFACT_DOWNLOADED)) == 1


def test_the_package_events_match_the_events_endpoint(
    client: TestClient,
    reviewer: TestClient,
    plan_id: str,
    database_url: str,
    jobs_schema: str,
) -> None:
    validate(client, reviewer, plan_id)
    job_id = export(reviewer, plan_id, mode="final").json()["id"]
    run_worker_once(database_url, jobs_schema)
    package_id = reviewer.get(f"{API}/jobs/{job_id}").json()["result_ref"]
    case_id = reviewer.get(f"{API}/plans/{plan_id}").json()["case_file_id"]

    data = reviewer.get(f"{API}/artifacts/{package_id}", params={"confirm": "true"}).content
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        packaged = json.loads(archive.read("events.json"))
    listed = reviewer.get(f"{API}/case-files/{case_id}/events").json()["items"]
    assert packaged == listed[: len(packaged)]
    assert verify_chain(packaged).valid


def test_missing_original_fails_the_job_with_cause_and_action(
    client: TestClient,
    plan_id: str,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
) -> None:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        source = db.get(Source, plan.primary_source_id)
        assert source is not None
        source.storage_key = key_for_hash("0" * 64)
        db.commit()
    job_id = export(client, plan_id, mode="draft").json()["id"]

    run_worker_once(database_url, jobs_schema)

    polled = client.get(f"{API}/jobs/{job_id}").json()
    assert polled["status"] == "failed"
    assert polled["error"] == MISSING_OBJECT.to_dict()
    assert artifacts(factory, plan_id) == []
    assert stored_plan(factory, plan_id).status is PlanStatus.EDITING


def test_failed_package_build_logs_the_error_type_not_its_message(
    client: TestClient,
    reviewer: TestClient,
    plan_id: str,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-11 / H6-8: export error messages may quote geometry values; logs keep the type."""
    leaked = "coords_m[1][0]: 4.2137 at croquis-secreto-07.jpg"

    def failing_build(_inputs: object) -> object:
        raise SnapshotError(leaked)

    validate(client, reviewer, plan_id)
    job_id = export(reviewer, plan_id, mode="final").json()["id"]
    monkeypatch.setattr(tasks_export, "build_package", failing_build)

    with caplog.at_level(logging.DEBUG):
        run_worker_once(database_url, jobs_schema)

    polled = reviewer.get(f"{API}/jobs/{job_id}").json()
    assert polled["status"] == "failed"
    assert polled["error"] == tasks_export.EXPORT_FAILED.to_dict()
    assert stored_plan(factory, plan_id).status is PlanStatus.VALIDATED
    assert "package build failed: SnapshotError" in caplog.text
    assert "4.2137" not in caplog.text and "croquis-secreto" not in caplog.text


def test_unknown_ids_are_404_and_invalid_bodies_422(client: TestClient, plan_id: str) -> None:
    unknown = uuid.uuid4()
    assert export(client, str(unknown), mode="draft").status_code == 404
    assert client.get(f"{API}/artifacts/{unknown}").status_code == 404
    assert client.get(f"{API}/artifacts/not-a-uuid").status_code == 404
    assert client.get(f"{API}/plans/{unknown}/artifacts").status_code == 404
    assert export(client, plan_id, mode="final-ish").status_code == 422
    assert export(client, plan_id, mode="draft", scale=75).status_code == 422
    assert export(client, plan_id, mode="draft", paper="A0").status_code == 422


def test_mark_exported_only_moves_a_validated_plan(
    plan_id: str, owner: User, factory: sessionmaker[Session]
) -> None:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        user = db.get(User, owner.id)
        assert plan is not None and user is not None
        with pytest.raises(TransitionConflictError):
            mark_exported(db, plan, user)
        db.rollback()
    set_status(factory, plan_id, PlanStatus.VALIDATED)
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        user = db.get(User, owner.id)
        assert plan is not None and user is not None
        mark_exported(db, plan, user, "10.0.0.1")
        db.commit()
    assert stored_plan(factory, plan_id).status is PlanStatus.EXPORTED
    event = events(factory, EventKind.PLAN_TRANSITION)[-1]
    assert (event.after, event.client_ip) == ({"status": "EXPORTED"}, "10.0.0.1")
