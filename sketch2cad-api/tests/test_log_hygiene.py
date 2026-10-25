"""H1-9 / REQ-11: logs of the upload and QC flow carry identifiers only."""

import codecs
import hashlib
import io
import logging
import re
import sys
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import ExifTags, Image
from psycopg import sql
from sqlalchemy import Engine
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db import session as db_session
from sketch2cad.db.base import Base
from sketch2cad.db.models import User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.logging import (
    QUIET_LOGGERS,
    KeyValueFormatter,
    PayloadRedactionFilter,
    configure_logging,
)
from sketch2cad.qc import synthetic
from sketch2cad.worker import tasks_raster
from sketch2cad.worker.app import apply_schema, create_worker_app, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
MARKER = f"marker{uuid.uuid4().hex}"
FILENAME = f"croquis-{MARKER}.jpg"
GPS_LATITUDE = (40.0, 25.0, 12.3456)
GPS_LONGITUDE = (3.0, 42.0, 7.8912)
SLICE = 16
SRC = Path(__file__).resolve().parents[1] / "src"
COORDINATE_PATTERNS = (
    re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*[,)]"),  # (x, y) tuples
    re.compile(r"\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*[,\]]"),  # [x, y] lists
    re.compile(r"\b(?:x|y|lat|lon|latitude|longitude|gps\w*)\s*[=:]\s*-?\d", re.IGNORECASE),
    re.compile(r"12\.3456|7\.8912"),  # the GPS seconds written into the EXIF
)
FORBIDDEN_LOG_CALL = re.compile(r"logger\.(info|debug|warning).*(filename|geometry|coords)")


@pytest.fixture(scope="module")
def schema(database_url: str) -> Iterator[str]:
    """Own schema holding the app and Procrastinate tables; dropped afterwards."""
    name = f"test_logs_{uuid.uuid4().hex}"
    admin = to_conninfo(database_url)
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name))
            )


@pytest.fixture(scope="module")
def worker_conninfo(database_url: str, schema: str) -> str:
    return to_conninfo(database_url, options=f"-csearch_path={schema}")


@pytest.fixture(scope="module")
def engine(database_url: str, schema: str, worker_conninfo: str) -> Iterator[Engine]:
    engine = create_db_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        apply_schema(worker_conninfo)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def factory(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    session_factory = create_session_factory(engine)
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
def all_loggers_at_debug(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Root and every non-quiet logger at DEBUG and propagating to ``caplog``."""
    configure_logging()
    caplog.set_level(logging.DEBUG)
    saved: list[tuple[logging.Logger, int, bool]] = []
    for name, item in logging.root.manager.loggerDict.items():
        quiet = any(name == q or name.startswith(f"{q}.") for q in QUIET_LOGGERS)
        if isinstance(item, logging.Logger) and not quiet:
            saved.append((item, item.level, item.propagate))
            item.setLevel(logging.DEBUG)
            item.propagate = True
    yield caplog
    for logger, level, propagate in saved:
        logger.setLevel(level)
        logger.propagate = propagate


def jpeg_with_marker_and_gps() -> bytes:
    """A synthetic sketch whose EXIF holds the marker and GPS coordinates."""
    image = Image.fromarray(synthetic.sketch(1200)[:, :, ::-1])
    exif = Image.Exif()
    exif[0x010F] = MARKER  # Make
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps.update(
        {
            ExifTags.GPS.GPSLatitudeRef: "N",
            ExifTags.GPS.GPSLatitude: GPS_LATITUDE,
            ExifTags.GPS.GPSLongitudeRef: "W",
            ExifTags.GPS.GPSLongitude: GPS_LONGITUDE,
        }
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif, quality=90)
    return buffer.getvalue()


def signed_in_client(app: FastAPI, stack: ExitStack, factory: sessionmaker[Session]) -> TestClient:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
        )
        db.add(user)
        db.commit()
    client = stack.enter_context(TestClient(app, base_url="https://testserver"))
    response = client.post(
        f"{API_V1_PREFIX}/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


def record_texts(records: list[logging.LogRecord]) -> list[str]:
    """Every textual form of the records: raw parts, the app formatter and a plain one."""
    app_formatter, plain = KeyValueFormatter(), logging.Formatter()
    texts: list[str] = []
    for record in records:
        texts += [record.name, record.getMessage(), repr(record.msg), repr(record.args)]
        texts += [repr(vars(record)), plain.format(record), app_formatter.format(record)]
    return texts


def byte_forms(text: str) -> list[bytes]:
    """``text`` as raw bytes, with hex runs and ``repr`` escapes decoded back to bytes."""
    forms = [text.encode("utf-8", "surrogateescape"), text.encode("latin-1", "ignore")]
    forms += [bytes.fromhex(run) for run in re.findall(r"(?:[0-9a-fA-F]{2}){16,}", text)]
    escaped = text.encode("latin-1", "backslashreplace")
    forms.append(codecs.escape_decode(escaped)[0])  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
    return forms


def leaks_image(text: str, windows: set[bytes]) -> bool:
    return any(
        form[i : i + SLICE] in windows
        for form in byte_forms(text)
        for i in range(len(form) - SLICE + 1)
    )


def test_upload_and_qc_logs_carry_identifiers_only(
    app: FastAPI,
    factory: sessionmaker[Session],
    worker_conninfo: str,
    all_loggers_at_debug: pytest.LogCaptureFixture,
) -> None:
    data = jpeg_with_marker_and_gps()
    with ExitStack() as stack:
        client = signed_in_client(app, stack, factory)
        created = client.post(
            CASES_URL,
            json={
                "incident_type": "traffic_collision",
                "incident_date": "2026-10-20",
                "description": "Colisión entre dos vehículos",
                "declared_location": "Av. Libertador 1200",
            },
        )
        assert created.status_code == 201, created.text
        uploaded = client.post(
            f"{CASES_URL}/{created.json()['id']}/sources",
            files={"file": (FILENAME, data, "application/octet-stream")},
            data={"client_sha256": hashlib.sha256(data).hexdigest()},
        )
        assert uploaded.status_code == 201, uploaded.text
        create_worker_app(worker_conninfo).run_worker(
            wait=False, install_signal_handlers=False, listen_notify=False
        )
        job = client.get(f"{API_V1_PREFIX}/jobs/{uploaded.json()['qc_job_id']}")
        assert job.status_code == 200 and job.json()["status"] == "succeeded", job.text

    records = all_loggers_at_debug.records
    names = {record.name for record in records}
    assert {"sketch2cad.sources.ingest", "sketch2cad.jobs.service"} <= names
    assert any(name.startswith(("httpx", "httpcore", "procrastinate")) for name in names)
    texts = record_texts(records) + [all_loggers_at_debug.text]
    windows = {data[i : i + SLICE] for i in range(len(data) - SLICE + 1)}
    for text in texts:
        assert MARKER not in text, text
        assert not [p.pattern for p in COORDINATE_PATTERNS if p.search(text)], text
        assert not leaks_image(text, windows), text


def test_the_image_slice_detector_finds_raw_hex_and_repr_forms() -> None:
    data = jpeg_with_marker_and_gps()
    windows = {data[i : i + SLICE] for i in range(len(data) - SLICE + 1)}
    piece = data[200 : 200 + SLICE]

    assert leaks_image(f"payload {piece.hex()}", windows)
    assert leaks_image(f"payload {piece!r}", windows)
    assert leaks_image(piece.decode("latin-1"), windows)
    assert not leaks_image("Ingested source 1 into case file 2", windows)


def test_payload_loggers_stay_quiet_even_at_debug() -> None:
    configure_logging("DEBUG")
    try:
        for name in QUIET_LOGGERS:
            assert not logging.getLogger(name).isEnabledFor(logging.INFO), name
        assert not logging.getLogger("PIL.TiffImagePlugin").isEnabledFor(logging.DEBUG)
    finally:
        configure_logging()


def test_formatter_redacts_bytes_and_exception_messages() -> None:
    record = logging.LogRecord(
        "sketch2cad.test", logging.ERROR, __file__, 1, "got %s", (b"\x89PNG secret",), None
    )
    try:
        raise ValueError(f"cannot read {FILENAME}")
    except ValueError:
        record.exc_info = sys.exc_info()
    PayloadRedactionFilter().filter(record)

    line = KeyValueFormatter().format(record)

    assert 'msg="got <bytes len=11>"' in line
    assert "exc_type=ValueError" in line and "test_log_hygiene.py" in line
    assert "secret" not in line and MARKER not in line


def test_no_log_call_mentions_filename_geometry_or_coords() -> None:
    matches = [
        f"{path.relative_to(SRC)}:{number}"
        for path in SRC.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if FORBIDDEN_LOG_CALL.search(line)
    ]

    assert matches == []
