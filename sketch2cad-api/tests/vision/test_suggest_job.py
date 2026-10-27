"""The ``suggest_lines`` job: enqueue, coordinates, entities, relaunch and V-08 (REQ-17, REQ-15)."""

import hashlib
import io
import math
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from psycopg import sql
from sqlalchemy import Engine, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db import session as db_session
from sketch2cad.db.base import Base
from sketch2cad.db.models import Entity, Event, JobMeta, JobStatus, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.geometry import scale_matrix
from sketch2cad.geometry.frame import LocalFrame
from sketch2cad.plans import local_frame
from sketch2cad.vision import convert
from sketch2cad.worker import tasks_raster, tasks_suggest
from sketch2cad.worker.app import apply_schema, create_worker_app, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
TOLERANCE_M = 0.05  # 0.5 mm of paper at 1:100: the skeleton moves endpoints by ~2 px

# --- Pure conversion, hand-computed --------------------------------------------------------


def test_rectified_px_go_to_paper_mm_then_to_provisional_metres() -> None:
    # (200, 300) px / 10 = (20, 30) mm; 1:100 => (20 * 100 / 1000, -30 * 100 / 1000) m.
    assert convert.px_to_paper_mm([(200.0, 300.0)]) == [(20.0, 30.0)]
    assert convert.paper_mm_to_local_m([(20.0, 30.0)], 100) == [(2.0, -3.0)]
    # A scaled drawing at 1:50: (20 * 50 / 1000, -30 * 50 / 1000) = (1.0, -1.5).
    assert convert.paper_mm_to_local_m([(20.0, 30.0)], 50) == [(1.0, -1.5)]


def test_a_local_frame_moves_and_rotates_the_suggestions() -> None:
    # Origin at paper (20, 30) mm = world (2, -3) m at 1:100, +X axis at 90 degrees.
    # Paper (30, 30) mm = world (3, -3); minus the origin (1, 0); rotated by -90 => (0, -1).
    frame = LocalFrame.create((20.0, 30.0), 90.0, 0.0)
    [(x, y)] = convert.paper_mm_to_local_m([(30.0, 30.0)], 100, frame)

    assert (x, y) == pytest.approx((0.0, -1.0), abs=1e-12)


def test_the_image_region_maps_back_to_the_original_pixels() -> None:
    # 0.5 mm/px scan: paper (20, 30) mm is original pixel (40, 60).
    assert convert.paper_mm_to_image_px([(20.0, 30.0)], scale_matrix(0.5)) == [(40.0, 60.0)]


def test_a_repeated_first_point_closes_the_polyline() -> None:
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    closed = convert.polyline_geometry(square)
    assert closed == {"kind": "polyline", "coords_m": [list(p) for p in square[:4]], "closed": True}
    # Fewer than 3 distinct vertices stay open; a single point is no polyline.
    assert convert.polyline_geometry([(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)]) == {
        "kind": "polyline",
        "coords_m": [[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]],
        "closed": False,
    }
    assert convert.polyline_geometry([(1.0, 1.0), (1.0, 1.0)]) is None


def test_convert_polyline_gives_geometry_and_region_with_the_provisional_scale() -> None:
    frame = convert.SuggestionFrame(None, None, scale_matrix(0.1))
    converted = convert.convert_polyline([(200.0, 300.0), (1200.0, 300.0)], frame)

    assert converted is not None
    assert converted.geometry["coords_m"] == [[2.0, -3.0], [12.0, -3.0]]
    assert converted.polygon_px == [[200.0, 300.0], [1200.0, 300.0]]
    assert convert.PROVISIONAL_PAPER_SCALE == local_frame.PROVISIONAL_PAPER_SCALE


# --- Job integration: real worker in-process ------------------------------------------------

# Known strokes on a 150 x 100 mm sheet scanned at 254 dpi (10 px/mm, rectified == scan).
LINE_PX = ((200, 300), (1200, 300))
SQUARE_PX = ((300, 600), (700, 600), (700, 900), (300, 900))


def sketch_png() -> bytes:
    image = np.full((1000, 1500), 255, np.uint8)
    cv2.line(image, LINE_PX[0], LINE_PX[1], 0, 5)
    cv2.polylines(image, [np.array(SQUARE_PX, np.int32)], True, 0, 5)
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG", dpi=(254, 254))
    return buffer.getvalue()


@pytest.fixture(scope="module")
def jobs_schema(database_url: str) -> Iterator[str]:
    schema = f"test_suggest_{uuid.uuid4().hex}"
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
    monkeypatch.delenv("AI_ASSIST_ENABLED", raising=False)
    return tmp_path / "storage"


def _login(stack: ExitStack, factory: sessionmaker[Session]) -> TestClient:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
        )
        db.add(user)
        db.commit()
        username = user.username
    app: FastAPI = create_app()
    client = stack.enter_context(TestClient(app, base_url="https://testserver"))
    response = client.post(
        f"{API_V1_PREFIX}/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def stack(storage_root: Path) -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


@pytest.fixture
def client(stack: ExitStack, factory: sessionmaker[Session]) -> TestClient:
    return _login(stack, factory)


def make_plan(client: TestClient, source_mode: str = "schematic_sketch") -> str:
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
    plan = {
        "title": "Plano general",
        "primary_source_id": response.json()["id"],
        "source_mode": source_mode,
    }
    response = client.post(f"{CASES_URL}/{case_id}/plans", json=plan)
    assert response.status_code == 201, response.text
    plan_id = response.json()["id"]
    response = client.post(f"{PLANS_URL}/{plan_id}/lease")
    assert response.status_code == 200, response.text
    return plan_id


def accept(client: TestClient, plan_id: str, status: int = 200, **body: Any) -> dict[str, Any]:
    payload = {"method": "scan_dpi", "as_metric": False, **body}
    response = client.post(f"{PLANS_URL}/{plan_id}/calibration/accept", json=payload)
    assert response.status_code == status, response.text
    return response.json()


def run_worker_once(database_url: str, jobs_schema: str) -> None:
    conninfo = to_conninfo(database_url, options=f"-csearch_path={jobs_schema}")
    create_worker_app(conninfo).run_worker(
        wait=False, install_signal_handlers=False, listen_notify=False
    )


def suggest_jobs(factory: sessionmaker[Session], plan_id: str) -> list[JobMeta]:
    with factory() as db:
        query = select(JobMeta).where(
            JobMeta.plan_id == uuid.UUID(plan_id), JobMeta.kind == tasks_suggest.SUGGEST_JOB_KIND
        )
        return list(db.scalars(query))


def live_suggestions(factory: sessionmaker[Session], plan_id: str) -> list[Entity]:
    with factory() as db:
        query = select(Entity).where(
            Entity.deleted_at.is_(None),
            Entity.source["method"].astext == "cv",
            Entity.revision_id.in_(
                sql_text("SELECT current_revision_id FROM plan WHERE id = :p").bindparams(
                    p=uuid.UUID(plan_id)
                )
            ),
        )
        return list(db.scalars(query))


def events(factory: sessionmaker[Session], plan_id: str, kind: str) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.plan_id == uuid.UUID(plan_id), Event.kind == kind)
        return list(db.scalars(query.order_by(Event.id)))


def relaunch(client: TestClient, plan_id: str, **body: Any) -> Any:
    return client.post(f"{PLANS_URL}/{plan_id}/jobs", json={"kind": "suggest_lines", **body})


def _close(a: list[float], b: tuple[float, float]) -> bool:
    return math.dist(a, b) <= TOLERANCE_M


def assert_known_strokes(entities: list[Entity], scale: float = 100.0) -> None:
    """The line and the square of :func:`sketch_png`, in metres at 1:``scale``."""

    def metres(p: tuple[int, int]) -> tuple[float, float]:
        return (p[0] / 10 * scale / 1000, -p[1] / 10 * scale / 1000)

    by_closed = {bool(e.geometry["closed"]): e.geometry["coords_m"] for e in entities}
    assert len(entities) == 2 and set(by_closed) == {True, False}
    line = by_closed[False]
    assert len(line) == 2
    ends = sorted(line)
    assert _close(ends[0], metres(LINE_PX[0])) and _close(ends[1], metres(LINE_PX[1]))
    square = by_closed[True]
    assert len(square) == 4
    for corner in SQUARE_PX:
        assert any(_close(vertex, metres(corner)) for vertex in square)


def test_creating_the_plan_queues_nothing_and_two_accepts_queue_one_job(
    client: TestClient, factory: sessionmaker[Session], database_url: str, jobs_schema: str
) -> None:
    plan_id = make_plan(client)
    assert suggest_jobs(factory, plan_id) == []

    accept(client, plan_id)
    accept(client, plan_id)  # same calibration, before the worker runs
    assert suggest_jobs(factory, plan_id) == []  # chained from the rectify job
    run_worker_once(database_url, jobs_schema)
    accept(client, plan_id)  # after it ran: same calibration, suggestions kept
    run_worker_once(database_url, jobs_schema)

    [job] = suggest_jobs(factory, plan_id)
    assert (job.status, job.error) == (JobStatus.SUCCEEDED.value, None)
    assert job.model_version == tasks_suggest.VISION_PIPELINE_VERSION
    assert job.source_id is not None and job.params["result"]["count"] == 2
    assert len(live_suggestions(factory, plan_id)) == 2
    response = client.get(f"{API_V1_PREFIX}/jobs/{job.id}")
    assert response.status_code == 200 and response.json()["kind"] == "suggest_lines"


def test_suggestions_are_pending_cv_trace_only_polylines_with_one_event(
    client: TestClient, factory: sessionmaker[Session], database_url: str, jobs_schema: str
) -> None:
    plan_id = make_plan(client)
    accept(client, plan_id)
    run_worker_once(database_url, jobs_schema)

    [job] = suggest_jobs(factory, plan_id)
    entities = live_suggestions(factory, plan_id)
    assert_known_strokes(entities)
    for entity in entities:
        assert (entity.type, entity.layer) == ("polyline", "L_STRUCTURE")
        assert (entity.review_status, entity.metric_origin) == ("suggested", "trace_only")
        assert entity.source == {"method": "cv", "job_id": str(job.id), "parent_entity_id": None}
        assert entity.created_by is None and entity.confidence is not None
        assert 0 <= entity.confidence <= 1
        assert entity.model_version == tasks_suggest.VISION_PIPELINE_VERSION
        assert entity.image_region is not None
        assert entity.image_region["source_id"] == str(job.source_id)
    [event] = events(factory, plan_id, "suggestions.added")
    assert event.after is not None and event.actor_id is None
    assert event.after["count"] == 2 and event.after["job_id"] == str(job.id)
    assert sorted(event.after["entity_ids"]) == sorted(str(e.id) for e in entities)
    assert events(factory, plan_id, "entity.created") == []


def test_a_metric_scaled_drawing_uses_its_scale_and_turns_approximate(
    client: TestClient, factory: sessionmaker[Session], database_url: str, jobs_schema: str
) -> None:
    plan_id = make_plan(client, "scaled_drawing")
    plan = accept(client, plan_id, paper_scale=50, as_metric=True)
    assert plan["metric_status"] == "metric"  # empty metric scaled drawing

    run_worker_once(database_url, jobs_schema)

    entities = live_suggestions(factory, plan_id)
    assert_known_strokes(entities, scale=50)
    status = client.get(f"{PLANS_URL}/{plan_id}/metric-status").json()
    assert status["status"] == "approximate"
    assert sorted(status["blocking_entities"]) == sorted(str(e.id) for e in entities)
    assert set(status["reasons"].values()) == {"trace_only"}
    assert client.get(f"{PLANS_URL}/{plan_id}").json()["metric_status"] == "approximate"


def test_method_none_runs_on_the_original_at_once(
    client: TestClient, factory: sessionmaker[Session], database_url: str, jobs_schema: str
) -> None:
    plan_id = make_plan(client)
    accept(client, plan_id, method="none")

    [job] = suggest_jobs(factory, plan_id)
    assert job.params["image"] == "original" and job.params["rectified_artifact_id"] is None
    run_worker_once(database_url, jobs_schema)

    assert_known_strokes(live_suggestions(factory, plan_id))  # 254 dpi source: 10 px/mm


def _accept_one(factory: sessionmaker[Session], plan_id: str) -> None:
    [first, *_] = live_suggestions(factory, plan_id)
    with factory() as db:
        db.execute(update(Entity).where(Entity.id == first.id).values(review_status="accepted"))
        db.commit()


def test_relaunch_is_refused_once_an_entity_is_accepted_or_manual(
    client: TestClient, factory: sessionmaker[Session], database_url: str, jobs_schema: str
) -> None:
    plan_id = make_plan(client)
    accept(client, plan_id)
    run_worker_once(database_url, jobs_schema)
    _accept_one(factory, plan_id)

    response = relaunch(client, plan_id)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "suggestions_locked"
    assert len(live_suggestions(factory, plan_id)) == 2
    assert accept(client, plan_id, 409, method="none")["code"] == "V-08"


def test_relaunch_replaces_pending_suggestions(
    client: TestClient, factory: sessionmaker[Session], database_url: str, jobs_schema: str
) -> None:
    plan_id = make_plan(client)
    accept(client, plan_id)
    run_worker_once(database_url, jobs_schema)
    old = {e.id for e in live_suggestions(factory, plan_id)}

    response = relaunch(client, plan_id)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert live_suggestions(factory, plan_id) == []
    [deleted] = events(factory, plan_id, "entity.deleted")
    assert deleted.before is not None and deleted.before["count"] == 2
    assert deleted.reason == "suggestions_relaunched"
    run_worker_once(database_url, jobs_schema)

    new = live_suggestions(factory, plan_id)
    assert len(new) == 2 and not old & {e.id for e in new}
    jobs = suggest_jobs(factory, plan_id)
    assert len(jobs) == 2 and all(j.status == JobStatus.SUCCEEDED.value for j in jobs)
    assert len(events(factory, plan_id, "suggestions.added")) == 2


def test_recalibration_with_only_pending_suggestions_clears_and_regenerates_them(
    client: TestClient, factory: sessionmaker[Session], database_url: str, jobs_schema: str
) -> None:
    plan_id = make_plan(client)
    accept(client, plan_id)
    run_worker_once(database_url, jobs_schema)
    old = {e.id for e in live_suggestions(factory, plan_id)}

    segment: dict[str, Any] = {
        "method": "scan_segment",
        "control_points": [{"image_px": [0, 0]}, {"image_px": [1000, 0]}],
        "segment_mm": 100.0,
    }
    accept(client, plan_id, **segment)  # V-08 ignores pending suggestions
    assert live_suggestions(factory, plan_id) == []
    [deleted] = events(factory, plan_id, "entity.deleted")
    assert deleted.reason == "recalibration"
    run_worker_once(database_url, jobs_schema)

    new = live_suggestions(factory, plan_id)
    assert_known_strokes(new)
    assert not old & {e.id for e in new}
    jobs = suggest_jobs(factory, plan_id)
    assert sorted(j.params.get("superseded_at") is None for j in jobs) == [False, True]


def test_a_superseded_queued_job_inserts_nothing(
    client: TestClient, factory: sessionmaker[Session], database_url: str, jobs_schema: str
) -> None:
    plan_id = make_plan(client)
    accept(client, plan_id, method="none")  # queued at once
    accept(client, plan_id, method="none", as_metric=False)  # same: no second job
    assert len(suggest_jobs(factory, plan_id)) == 1
    accept(client, plan_id)  # scan_dpi: different calibration, supersedes the queued job

    run_worker_once(database_url, jobs_schema)

    jobs = {j.params["image"]: j for j in suggest_jobs(factory, plan_id)}
    assert jobs["original"].params["result"] == {"count": 0, "skipped": "superseded"}
    assert jobs["rectified"].params["result"]["count"] == 2
    assert len(live_suggestions(factory, plan_id)) == 2


def test_ai_assist_disabled_queues_nothing_and_hides_the_relaunch(
    client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_ASSIST_ENABLED", "false")
    plan_id = make_plan(client)
    accept(client, plan_id)
    run_worker_once(database_url, jobs_schema)

    assert suggest_jobs(factory, plan_id) == []
    assert relaunch(client, plan_id).status_code == 404


def test_a_missing_rectified_image_fails_with_cause_and_action(
    client: TestClient,
    factory: sessionmaker[Session],
    storage_root: Path,
    database_url: str,
    jobs_schema: str,
) -> None:
    plan_id = make_plan(client)
    accept(client, plan_id)
    run_worker_once(database_url, jobs_schema)
    for path in storage_root.rglob("*"):
        if path.is_file():
            path.unlink()

    assert relaunch(client, plan_id).status_code == 202
    run_worker_once(database_url, jobs_schema)

    failed = [j for j in suggest_jobs(factory, plan_id) if j.status == JobStatus.FAILED.value]
    assert [j.error for j in failed] == [tasks_suggest.RECTIFIED_MISSING.to_dict()]
    assert live_suggestions(factory, plan_id) == []
    response = client.get(f"{API_V1_PREFIX}/jobs/{failed[0].id}")
    assert response.json()["error"] == tasks_suggest.RECTIFIED_MISSING.to_dict()
    assert client.get(f"{PLANS_URL}/{plan_id}").json()["status"] == "EDITING"


def test_relaunch_needs_membership_lease_calibration_and_no_params(
    client: TestClient,
    stack: ExitStack,
    factory: sessionmaker[Session],
) -> None:
    plan_id = make_plan(client)
    response = relaunch(client, plan_id)
    assert response.status_code == 409 and response.json()["code"] == "calibration_required"
    accept(client, plan_id)
    response = relaunch(client, plan_id)  # the rectify job has not run yet
    assert response.status_code == 409 and response.json()["code"] == "rectified_image_pending"
    assert relaunch(client, plan_id, params={"threshold_c": 5}).status_code == 422
    assert client.post(f"{PLANS_URL}/{plan_id}/jobs", json={"kind": "ocr"}).status_code == 422

    outsider = _login(stack, factory)
    assert relaunch(outsider, plan_id).status_code == 403
    assert client.delete(f"{PLANS_URL}/{plan_id}/lease").status_code == 200
    response = relaunch(client, plan_id)
    assert response.status_code == 409 and response.json()["code"] == "lease_required"


def test_worker_app_registers_the_suggest_task() -> None:
    app = create_worker_app("dbname=unused")
    tasks = cast(dict[str, Any], app.tasks)  # pyright: ignore[reportUnknownMemberType]

    assert tasks[tasks_suggest.SUGGEST_TASK_NAME].func is tasks_suggest.suggest_lines
