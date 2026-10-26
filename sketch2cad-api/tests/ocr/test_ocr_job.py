"""The ``ocr_crop`` job and its API (REQ-18, US-13; spec section 10).

Fast tests replace :func:`sketch2cad.ocr.trocr.recognize` with a fake; the ``slow`` test
runs the real TrOCR model and is skipped when the ``ocr`` extra or the model files are
missing (``uv sync --extra ocr`` and ``TROCR_MODEL_DIR`` pointing at a local copy).
"""

import hashlib
import io
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
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
from sketch2cad.db.models import Event, JobMeta, JobStatus, Source, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.ocr import trocr
from sketch2cad.worker import tasks_ocr, tasks_raster
from sketch2cad.worker.app import apply_schema, create_worker_app, to_conninfo

API_DIR = Path(__file__).resolve().parents[2]
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
WIDTH, HEIGHT = 1200, 600
NUMBER_BOX = [380, 180, 820, 420]  # loose box around the number drawn by sketch_png
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

# --- Pure parts: no database, no model ----------------------------------------------------


@pytest.mark.parametrize(
    ("box", "message"),
    [
        ([0, 0, 1201, 10], "inside the image"),
        ([-1, 0, 10, 10], "inside the image"),
        ([10, 10, 10, 20], "inside the image"),
        ([20, 10, 10, 20], "inside the image"),
        ([0, 0, 7, 100], "at least 8"),
        ([0, 0, 1200, 100], None),
        ([0, 0, 10, 10, 10], "four integers"),
        ([0.0, 0, 10, 10], "four integers"),
        ([True, 0, 10, 10], "four integers"),
    ],
)
def test_validate_box_keeps_the_crop_inside_the_image(box: list[Any], message: str | None) -> None:
    if message is None:
        assert trocr.validate_box(box, WIDTH, HEIGHT) == tuple(box)
        return
    with pytest.raises(trocr.CropError, match=message):
        trocr.validate_box(box, WIDTH, HEIGHT)


def test_validate_box_refuses_page_sized_crops() -> None:
    with pytest.raises(trocr.CropError, match="at most 2000"):
        trocr.validate_box([0, 0, 2001, 100], 3000, 3000)


def test_prepare_crop_tightens_to_the_ink_with_a_margin_and_downscales() -> None:
    image = Image.new("L", (3000, 400), 255)
    ImageDraw.Draw(image).rectangle((1000, 100, 1399, 199), fill=0)  # 400 x 100 px of ink

    prepared = trocr.prepare_crop(image)

    assert prepared.mode == "RGB"
    assert prepared.size == (450, 150)  # margin 25 px = 0.25 x 100 px ink height
    wide = Image.new("L", (2400, 300), 255)
    ImageDraw.Draw(wide).rectangle((0, 0, 2399, 9), fill=0)
    assert max(trocr.prepare_crop(wide).size) == trocr.MAX_INPUT_SIDE_PX
    blank = Image.new("L", (40, 20), 250)
    assert trocr.prepare_crop(blank).size == (40, 20)  # no ink: left as is


def _fake_recognizer(
    monkeypatch: pytest.MonkeyPatch, text: str, confidence: float
) -> list[Image.Image]:
    seen: list[Image.Image] = []

    def recognize(image: Image.Image) -> tuple[str, float]:
        seen.append(image)
        return text, confidence

    monkeypatch.setattr(trocr, "recognize", recognize)
    return seen


@pytest.mark.parametrize(
    ("raw", "confidence", "value_m", "text", "preselected"),
    [
        ("3, 50", 0.82, 3.5, "3.50", True),
        ("3,50 mts.", 0.6, 3.5, "3.50", True),  # inclusive threshold
        ("350 cm", 0.59, 3.5, "350 cm", False),
        ("abc", 0.99, None, "abc", False),
    ],
)
def test_ocr_crop_normalizes_and_preselects(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    confidence: float,
    value_m: float | None,
    text: str,
    preselected: bool,
) -> None:
    _fake_recognizer(monkeypatch, raw, confidence)

    result = trocr.ocr_crop(Image.new("RGB", (100, 50), "white"), [0, 0, 100, 50])
    out = result.to_dict(0.6)

    assert (out["value_m"], out["text"], out["raw_text"]) == (value_m, text, raw)
    assert out["confidence"] == confidence and out["preselect"] is preselected
    assert out["model_version"] == trocr.OCR_MODEL_VERSION
    assert out["reason"] == (None if value_m is not None else "non_numeric")


def test_the_worker_image_downloads_the_pinned_revision() -> None:
    dockerfile = (API_DIR / "Dockerfile.worker").read_text(encoding="utf-8")

    assert f"ARG TROCR_REVISION={trocr.TROCR_REVISION}" in dockerfile
    assert "HF_HUB_OFFLINE=1" in dockerfile and "TRANSFORMERS_OFFLINE=1" in dockerfile
    assert trocr.OCR_MODEL_VERSION == f"trocr-base-handwritten@{trocr.TROCR_REVISION}"


def test_the_api_and_worker_modules_do_not_import_torch() -> None:
    code = (
        "import sys, sketch2cad.api, sketch2cad.worker.app, sketch2cad.worker.tasks_ocr; "
        "assert not {'torch', 'transformers'} & set(sys.modules), 'heavy import'"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_a_missing_model_directory_is_model_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(trocr, "_loaded", None)
    monkeypatch.setenv(trocr.MODEL_DIR_ENV, str(tmp_path / "missing"))

    with pytest.raises(trocr.ModelUnavailableError):
        trocr.load_model()


def test_the_worker_preload_only_warns_when_the_model_is_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def missing() -> None:
        raise trocr.ModelUnavailableError("model directory /nowhere does not exist")

    monkeypatch.setattr(trocr, "load_model", missing)

    tasks_ocr.preload_model_in_background().join(timeout=5)

    assert "OCR model not loaded at worker start" in caplog.text


# --- Job and API: real worker in-process, fake recognizer ----------------------------------


def sketch_png(number: str = "3,50", font: ImageFont.FreeTypeFont | None = None) -> bytes:
    """A white sheet with two strokes and ``number`` near the centre of NUMBER_BOX."""
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.line((100, 500, 1100, 500), fill="black", width=5)
    draw.line((100, 100, 100, 500), fill="black", width=5)
    shown = font or ImageFont.load_default(96)
    draw.text((600, 300), number, fill="black", font=shown, anchor="mm")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def jobs_schema(database_url: str) -> Iterator[str]:
    schema = f"test_ocr_{uuid.uuid4().hex}"
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
    monkeypatch.delenv("OCR_MIN_CONFIDENCE", raising=False)
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


def login(stack: ExitStack, factory: sessionmaker[Session]) -> TestClient:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
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
    return login(stack, factory)


def upload(client: TestClient, case_id: str, data: bytes) -> str:
    response = client.post(
        f"{CASES_URL}/{case_id}/sources",
        files={"file": ("croquis.png", data, "application/octet-stream")},
        data={"client_sha256": hashlib.sha256(data).hexdigest()},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_plan(client: TestClient) -> tuple[str, str, str]:
    """Case with one plan on a sketch; returns (case id, plan id, primary source id)."""
    case = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=case)
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]
    source_id = upload(client, case_id, sketch_png())
    plan = {"title": "Plano", "primary_source_id": source_id, "source_mode": "schematic_sketch"}
    response = client.post(f"{CASES_URL}/{case_id}/plans", json=plan)
    assert response.status_code == 201, response.text
    return case_id, response.json()["id"], source_id


def post_ocr(client: TestClient, plan_id: str, source_id: str, box: list[Any]) -> Any:
    body = {"kind": "ocr_crop", "params": {"source_id": source_id, "box_px": box}}
    return client.post(f"{PLANS_URL}/{plan_id}/jobs", json=body)


def run_worker_once(database_url: str, jobs_schema: str) -> None:
    conninfo = to_conninfo(database_url, options=f"-csearch_path={jobs_schema}")
    create_worker_app(conninfo).run_worker(
        wait=False, install_signal_handlers=False, listen_notify=False
    )


def test_the_job_reads_the_crop_and_get_job_returns_the_result(
    client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_recognizer(monkeypatch, "3, 50", 0.82)
    _, plan_id, source_id = make_plan(client)

    response = post_ocr(client, plan_id, source_id, NUMBER_BOX)  # no lease, no calibration

    assert response.status_code == 202, response.text
    queued = response.json()
    assert (queued["kind"], queued["status"], queued["result"]) == ("ocr_crop", "queued", None)
    assert queued["source_id"] == source_id
    run_worker_once(database_url, jobs_schema)

    job = client.get(f"{API_V1_PREFIX}/jobs/{queued['id']}").json()
    assert (job["status"], job["error"]) == ("succeeded", None)
    assert job["result"] == {
        "text": "3.50",
        "raw_text": "3, 50",
        "value_m": 3.5,
        "unit_detected": None,
        "reason": None,
        "confidence": 0.82,
        "preselect": True,
        "min_confidence": 0.6,
        "model_version": trocr.OCR_MODEL_VERSION,
    }
    [image] = seen  # the crop, tightened to the drawn number (strokes are outside the box)
    assert image.width < NUMBER_BOX[2] - NUMBER_BOX[0]
    assert image.height < NUMBER_BOX[3] - NUMBER_BOX[1]
    with factory() as db:
        stored = db.get(JobMeta, uuid.UUID(queued["id"]))
        assert stored is not None and stored.model_version == trocr.OCR_MODEL_VERSION
        assert stored.params["box_px"] == NUMBER_BOX
        assert list(db.scalars(select(Event.kind).where(Event.plan_id == stored.plan_id))) == [
            "plan.created"
        ]


def test_the_threshold_comes_from_ocr_min_confidence(
    client: TestClient,
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_recognizer(monkeypatch, "3,50", 0.82)
    monkeypatch.setenv("OCR_MIN_CONFIDENCE", "0.9")
    _, plan_id, source_id = make_plan(client)
    job_id = post_ocr(client, plan_id, source_id, NUMBER_BOX).json()["id"]
    run_worker_once(database_url, jobs_schema)

    result = client.get(f"{API_V1_PREFIX}/jobs/{job_id}").json()["result"]
    assert (result["value_m"], result["min_confidence"], result["preselect"]) == (3.5, 0.9, False)


@pytest.mark.parametrize(
    ("box", "code"),
    [
        ([0, 0, WIDTH + 1, 100], "ocr_box_invalid"),
        ([500, 300, 400, 400], "ocr_box_invalid"),
        ([0, 0, 4, 4], "ocr_box_invalid"),
        ([0, 0, 10.5, 10], None),
        ([0, 0, 10], None),
    ],
)
def test_a_box_outside_the_image_or_malformed_is_422(
    client: TestClient, factory: sessionmaker[Session], box: list[Any], code: str | None
) -> None:
    _, plan_id, source_id = make_plan(client)

    response = post_ocr(client, plan_id, source_id, box)

    assert response.status_code == 422, response.text
    assert response.json().get("code") == code
    with factory() as db:
        assert list(db.scalars(select(JobMeta).where(JobMeta.kind == "ocr_crop"))) == []


def test_the_source_must_be_the_primary_source_of_the_plan(client: TestClient) -> None:
    case_id, plan_id, _ = make_plan(client)
    other = upload(client, case_id, sketch_png("7,25"))

    for source_id in (other, str(uuid.uuid4())):
        response = post_ocr(client, plan_id, source_id, NUMBER_BOX)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "ocr_source_invalid"
    body = {"kind": "ocr_crop", "params": {"source_id": other, "box_px": NUMBER_BOX, "x": 1}}
    assert client.post(f"{PLANS_URL}/{plan_id}/jobs", json=body).status_code == 422


def test_the_job_checks_the_box_against_the_decoded_image(
    client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_recognizer(monkeypatch, "3,50", 0.9)
    _, plan_id, source_id = make_plan(client)
    with factory() as db:  # stale recorded size: the request check passes, the job does not
        source = db.get(Source, uuid.UUID(source_id))
        assert source is not None
        source.width_px = 5000
        db.commit()
    job_id = post_ocr(client, plan_id, source_id, [1000, 0, 1500, 100]).json()["id"]
    run_worker_once(database_url, jobs_schema)

    job = client.get(f"{API_V1_PREFIX}/jobs/{job_id}").json()
    assert (job["status"], job["result"]) == ("failed", None)
    assert job["error"] == tasks_ocr.BOX_OUTSIDE.to_dict()
    assert seen == []


def test_a_missing_model_fails_the_job_with_cause_and_action(
    client: TestClient,
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(image: Image.Image) -> tuple[str, float]:
        raise trocr.ModelUnavailableError("the `ocr` extra is not installed")

    monkeypatch.setattr(trocr, "recognize", unavailable)
    _, plan_id, source_id = make_plan(client)
    job_id = post_ocr(client, plan_id, source_id, NUMBER_BOX).json()["id"]
    run_worker_once(database_url, jobs_schema)

    job = client.get(f"{API_V1_PREFIX}/jobs/{job_id}").json()
    assert job["status"] == "failed" and job["error"] == tasks_ocr.MODEL_UNAVAILABLE.to_dict()


def test_ocr_needs_edit_permission_csrf_and_ai_assist(
    client: TestClient,
    stack: ExitStack,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, plan_id, source_id = make_plan(client)

    outsider = login(stack, factory)
    assert post_ocr(outsider, plan_id, source_id, NUMBER_BOX).status_code == 403
    token = client.headers.pop(CSRF_HEADER)
    assert post_ocr(client, plan_id, source_id, NUMBER_BOX).status_code == 403
    client.headers[CSRF_HEADER] = token
    monkeypatch.setenv("AI_ASSIST_ENABLED", "false")
    assert post_ocr(client, plan_id, source_id, NUMBER_BOX).status_code == 404
    with factory() as db:
        assert list(db.scalars(select(JobMeta).where(JobMeta.kind == "ocr_crop"))) == []


def _dimension(declared: float, ocr_job_id: str | None, kind: str = "dimension") -> Any:
    if kind == "line":
        geometry: dict[str, Any] = {"kind": "line", "coords_m": [[0, 0], [3.5, 0]]}
    else:
        geometry = {"kind": "dimension", "p1_m": [0, 0], "p2_m": [3.5, 0]}
        geometry["declared_value_m"] = declared
    layer = "L_DIMENSIONS" if kind == "dimension" else "L_STRUCTURE"
    return {"type": kind, "layer": layer, "geometry": geometry, "ocr_job_id": ocr_job_id}


def test_a_dimension_from_an_ocr_reading_records_its_review(
    client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    jobs_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_recognizer(monkeypatch, "3,50", 0.82)
    _, plan_id, source_id = make_plan(client)
    job_id = post_ocr(client, plan_id, source_id, NUMBER_BOX).json()["id"]
    pending = post_ocr(client, plan_id, source_id, NUMBER_BOX).json()["id"]
    run_worker_once(database_url, jobs_schema)
    with factory() as db:  # a job that has not run yet
        job = db.get(JobMeta, uuid.UUID(pending))
        assert job is not None
        job.status = JobStatus.QUEUED.value
        db.commit()
    assert client.post(f"{PLANS_URL}/{plan_id}/lease").status_code == 200
    calibration = {"method": "none", "as_metric": False}
    response = client.post(f"{PLANS_URL}/{plan_id}/calibration/accept", json=calibration)
    assert response.status_code == 200, response.text
    url = f"{API_V1_PREFIX}/revisions/{response.json()['current_revision_id']}/entities"

    for bad in (_dimension(3.5, job_id, "line"), _dimension(3.5, pending)):
        response = client.post(url, json={"entities": [bad]})
        assert response.status_code == 422, response.text
    body = {"entities": [_dimension(3.5, job_id), _dimension(3.6, job_id), _dimension(2, None)]}
    response = client.post(url, json=body)

    assert response.status_code == 201, response.text
    accepted, corrected, manual = response.json()["entities"]
    expected = {"method": "ocr", "job_id": job_id, "parent_entity_id": None}
    assert (accepted["source"], accepted["review_status"]) == (expected, "accepted")
    assert (corrected["source"], corrected["review_status"]) == (expected, "corrected")
    assert accepted["confidence"] == 0.82 and accepted["model_version"] == trocr.OCR_MODEL_VERSION
    assert (manual["source"]["method"], manual["review_status"]) == ("manual", "accepted")
    with factory() as db:
        query = select(Event).where(Event.kind == "entity.reviewed").order_by(Event.id)
        reviewed = list(db.scalars(query))
    assert [str(e.entity_id) for e in reviewed] == [accepted["id"], corrected["id"]]
    assert reviewed[0].before == {"review_status": "suggested", "value_m": 3.5}
    assert reviewed[0].after is not None and reviewed[0].after["review_status"] == "accepted"
    assert reviewed[1].after is not None and reviewed[1].after["declared_value_m"] == 3.6


# --- Real model (slow) --------------------------------------------------------------------


def _model_available() -> bool:
    configured = os.environ.get(trocr.MODEL_DIR_ENV)
    if configured:
        return Path(configured, "model.safetensors").is_file()
    return Path(trocr.DEFAULT_MODEL_DIR, "model.safetensors").is_file()


def _font() -> ImageFont.FreeTypeFont | None:
    for candidate in (os.environ.get("OCR_TEST_FONT"), *FONT_CANDIDATES):
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, 96)
    return None


@pytest.mark.slow
def test_a_rendered_3_50_reads_3_5_in_under_3_seconds_on_cpu() -> None:
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    if not _model_available():
        pytest.skip(f"TrOCR model files not found; set {trocr.MODEL_DIR_ENV}")
    font = _font()
    if font is None:
        pytest.skip("no DejaVu Sans or Arial font to render the number")
    image = Image.open(io.BytesIO(sketch_png("3,50", font))).convert("RGB")

    started = time.perf_counter()
    trocr.load_model()
    trocr.recognize(Image.new("RGB", (64, 32), "white"))  # warm-up
    cold = time.perf_counter() - started
    started = time.perf_counter()
    result = trocr.ocr_crop(image, NUMBER_BOX)
    warm = time.perf_counter() - started

    print(f"\nTrOCR cold load + warm-up {cold:.2f} s; ocr_crop {warm:.3f} s; {result}")
    assert result.value_m == 3.5, result
    assert warm < 3.0
