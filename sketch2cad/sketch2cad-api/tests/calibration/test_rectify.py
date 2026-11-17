"""The ``rectify`` job: rectified canvas in paper mm and its idempotent enqueue (REQ-03)."""

import hashlib
import io
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import numpy.typing as npt
import psycopg
import pytest
from fastapi import FastAPI
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
from sketch2cad.calibration import rectify
from sketch2cad.db import session as db_session
from sketch2cad.db.base import Base
from sketch2cad.db.models import Artifact, JobMeta, JobStatus, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.geometry import (
    homography_from_4_points,
    rectangle_corners_mm,
    scale_matrix,
    scan_dpi_scale,
)
from sketch2cad.storage import FilesystemStorage
from sketch2cad.worker import tasks_raster, tasks_rectify
from sketch2cad.worker.app import apply_schema, create_worker_app, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
PAPER_W_MM, PAPER_H_MM = 200.0, 150.0
PX_PER_MM = 10
DOTS_MM = [(x, y) for x in (25.0, 100.0, 175.0) for y in (25.0, 75.0, 125.0)]
# Where the paper corners (TL, TR, BR, BL) land in the simulated 2400 x 1800 px photo.
PHOTO_SIZE = (2400, 1800)
PHOTO_CORNERS_PX = [(420.0, 260.0), (2010.0, 380.0), (2150.0, 1600.0), (300.0, 1450.0)]


def flat_pattern() -> npt.NDArray[np.uint8]:
    """The 200 x 150 mm sheet at 10 px/mm: white paper with black dots at ``DOTS_MM``."""
    image = np.full((int(PAPER_H_MM) * PX_PER_MM, int(PAPER_W_MM) * PX_PER_MM), 255, np.uint8)
    for x, y in DOTS_MM:
        cv2.circle(image, (round(x * PX_PER_MM), round(y * PX_PER_MM)), 20, 0, -1, cv2.LINE_AA)
    return image


def oblique_photo() -> npt.NDArray[np.uint8]:
    """The sheet photographed obliquely on a mid-grey desk (``cv2.warpPerspective``)."""
    sheet_px = [(x * PX_PER_MM, y * PX_PER_MM) for x, y in rectangle_corners_mm(200, 150)]
    to_photo = homography_from_4_points(sheet_px, PHOTO_CORNERS_PX)
    photo = cv2.warpPerspective(flat_pattern(), to_photo, PHOTO_SIZE, borderValue=(128,))
    return np.asarray(photo, dtype=np.uint8)


def dot_centroids_px(image: npt.NDArray[np.uint8]) -> npt.NDArray[np.float64]:
    _, ink = cv2.threshold(image, 64, 255, cv2.THRESH_BINARY_INV)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 200]
    return np.asarray(centroids[keep], dtype=np.float64)


def test_rectified_oblique_photo_measures_2000_by_1500_px_and_keeps_distances() -> None:
    matrix = homography_from_4_points(PHOTO_CORNERS_PX, rectangle_corners_mm(200, 150))
    control = [{"role": "control", "paper_mm": list(p)} for p in rectangle_corners_mm(200, 150)]
    size = rectify.paper_size_mm("photo_homography_4pt", matrix.tolist(), control, PHOTO_SIZE)

    rectified = rectify.rectify_image(oblique_photo(), matrix, size)

    height, width = rectified.shape
    assert abs(width - 2000) <= 2 and abs(height - 1500) <= 2
    found_mm = dot_centroids_px(rectified) / PX_PER_MM
    assert len(found_mm) == len(DOTS_MM)
    for truth in DOTS_MM:
        assert float(np.hypot(*(found_mm - truth).T).min()) <= 1.0


def test_scan_canvas_is_the_whole_image_at_its_scale() -> None:
    mm_per_px = scan_dpi_scale(254)  # 10 px/mm: the rectified scan keeps its pixels
    matrix = scale_matrix(mm_per_px).tolist()
    scan = flat_pattern()
    height, width = scan.shape

    size = rectify.paper_size_mm("scan_dpi", matrix, [], (width, height))
    rectified = rectify.rectify_image(scan, matrix, size)

    assert size == pytest.approx((PAPER_W_MM, PAPER_H_MM))
    assert rectified.shape == (1500, 2000)
    assert int(np.abs(rectified.astype(int) - scan.astype(int)).max()) <= 1


def test_colour_pixels_keep_their_channels_and_the_outside_is_white() -> None:
    pixels = np.zeros((100, 100, 3), np.uint8)
    rectified = rectify.rectify_image(pixels, scale_matrix(0.5), (100.0, 60.0))

    assert rectified.shape == (600, 1000, 3)
    assert rectified[0, 0].tolist() == [0, 0, 0] and rectified[-1, -1].tolist() == [255] * 3


@pytest.mark.parametrize(
    ("method", "control"),
    [("none", []), ("photo_homography_4pt", []), ("photo_homography_4pt", [{"paper_mm": None}])],
)
def test_calibrations_without_a_canvas_are_rejected(
    method: str, control: list[dict[str, Any]]
) -> None:
    with pytest.raises(rectify.RectifyError):
        rectify.paper_size_mm(method, scale_matrix(1.0).tolist(), control, (10, 10))


def test_a_canvas_over_the_pixel_budget_is_rejected() -> None:
    with pytest.raises(rectify.RectifyError, match="too large"):
        rectify.rectify_image(np.zeros((4, 4), np.uint8), scale_matrix(1.0), (1e5, 1e5))


# --- Job: enqueue on acceptance, worker run, idempotency ---------------------------------


@pytest.fixture(scope="module")
def jobs_schema(database_url: str) -> Iterator[str]:
    schema = f"test_rectify_{uuid.uuid4().hex}"
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
def client(storage_root: Path, factory: sessionmaker[Session]) -> Iterator[TestClient]:
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
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(app, base_url="https://testserver"))
        response = client.post(
            f"{API_V1_PREFIX}/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
        yield client


def acquire_lease(client: TestClient, plan_id: object) -> None:
    """Take the plan's edit lease, required for every content edit (task 054)."""
    response = client.post(f"{API_V1_PREFIX}/plans/{plan_id}/lease")
    assert response.status_code == 200, response.text


@pytest.fixture
def plan_id(client: TestClient) -> str:
    case = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=case)
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]
    ok, buffer = cv2.imencode(".png", oblique_photo())
    assert ok
    data = buffer.tobytes()
    response = client.post(
        f"{CASES_URL}/{case_id}/sources",
        files={"file": ("foto.png", data, "application/octet-stream")},
        data={"client_sha256": hashlib.sha256(data).hexdigest()},
    )
    assert response.status_code == 201, response.text
    plan = {"title": "Plano general", "primary_source_id": response.json()["id"]}
    response = client.post(f"{CASES_URL}/{case_id}/plans", json=plan)
    assert response.status_code == 201, response.text
    acquire_lease(client, response.json()["id"])
    return response.json()["id"]


def accept(client: TestClient, plan_id: str, corners: list[tuple[float, float]]) -> None:
    body = {
        "method": "photo_homography_4pt",
        "control_points": [{"image_px": list(p)} for p in corners],
        "known_dimensions_mm": {"width": PAPER_W_MM, "height": PAPER_H_MM},
        "as_metric": False,
    }
    response = client.post(f"{PLANS_URL}/{plan_id}/calibration/accept", json=body)
    assert response.status_code == 200, response.text


def rectify_jobs(factory: sessionmaker[Session], plan_id: str) -> list[JobMeta]:
    with factory() as db:
        query = select(JobMeta).where(
            JobMeta.plan_id == uuid.UUID(plan_id), JobMeta.kind == rectify.RECTIFY_JOB_KIND
        )
        return list(db.scalars(query))


def artifacts(factory: sessionmaker[Session], plan_id: str) -> list[Artifact]:
    with factory() as db:
        return list(db.scalars(select(Artifact).where(Artifact.plan_id == uuid.UUID(plan_id))))


def run_worker_once(database_url: str, jobs_schema: str) -> None:
    conninfo = to_conninfo(database_url, options=f"-csearch_path={jobs_schema}")
    create_worker_app(conninfo).run_worker(
        wait=False, install_signal_handlers=False, listen_notify=False
    )


def test_accepting_twice_queues_one_job_that_stores_one_rectified_image(
    client: TestClient,
    plan_id: str,
    factory: sessionmaker[Session],
    storage_root: Path,
    database_url: str,
    jobs_schema: str,
) -> None:
    accept(client, plan_id, PHOTO_CORNERS_PX)
    accept(client, plan_id, PHOTO_CORNERS_PX)  # before the worker runs
    [job] = rectify_jobs(factory, plan_id)
    assert job.status == JobStatus.QUEUED.value and job.source_id is not None

    run_worker_once(database_url, jobs_schema)
    accept(client, plan_id, PHOTO_CORNERS_PX)  # after it ran

    [job] = rectify_jobs(factory, plan_id)
    assert (job.status, job.error) == (JobStatus.SUCCEEDED.value, None)
    [artifact] = artifacts(factory, plan_id)
    assert job.result_ref == str(artifact.id)
    assert (artifact.kind, artifact.revision_id, artifact.is_draft) == (
        "rectified_image",
        None,
        False,
    )
    data = FilesystemStorage(storage_root).get(artifact.storage_key)
    assert hashlib.sha256(data).hexdigest() == artifact.sha256 and len(data) == artifact.byte_size
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG" and image.size == (2000, 1500)


def test_a_different_calibration_of_an_empty_plan_gets_its_own_job(
    client: TestClient,
    plan_id: str,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
) -> None:
    accept(client, plan_id, PHOTO_CORNERS_PX)
    moved = [(x + 3.0, y) for x, y in PHOTO_CORNERS_PX]
    accept(client, plan_id, moved)

    run_worker_once(database_url, jobs_schema)

    jobs = rectify_jobs(factory, plan_id)
    assert [job.status for job in jobs] == [JobStatus.SUCCEEDED.value] * 2
    assert len({job.params["calibration_id"] for job in jobs}) == 2
    assert len(artifacts(factory, plan_id)) == 2


def test_method_none_queues_no_rectify_job(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    body = {"method": "none", "as_metric": False}
    response = client.post(f"{PLANS_URL}/{plan_id}/calibration/accept", json=body)

    assert response.status_code == 200, response.text
    assert rectify_jobs(factory, plan_id) == []


def test_missing_original_fails_with_cause_and_action(
    client: TestClient,
    plan_id: str,
    factory: sessionmaker[Session],
    storage_root: Path,
    database_url: str,
    jobs_schema: str,
) -> None:
    accept(client, plan_id, PHOTO_CORNERS_PX)
    for path in storage_root.rglob("*"):
        if path.is_file():
            path.unlink()

    run_worker_once(database_url, jobs_schema)

    [job] = rectify_jobs(factory, plan_id)
    assert job.status == JobStatus.FAILED.value
    assert job.error == rectify.MISSING_OBJECT.to_dict()
    assert artifacts(factory, plan_id) == []
    accept(client, plan_id, PHOTO_CORNERS_PX)  # a failed job does not block a new one
    assert len(rectify_jobs(factory, plan_id)) == 2


def test_worker_app_registers_the_rectify_task() -> None:
    app = create_worker_app("dbname=unused")
    tasks = cast(dict[str, Any], app.tasks)  # pyright: ignore[reportUnknownMemberType]

    assert tasks[rectify.RECTIFY_TASK_NAME].func is tasks_rectify.rectify
