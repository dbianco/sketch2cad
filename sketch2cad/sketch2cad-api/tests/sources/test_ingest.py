"""Source ingestion: V-01 (415), V-02 (413), V-03 (422), storage, events and the qc job."""

import asyncio
import hashlib
import io
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import psycopg
import pypdfium2  # pyright: ignore[reportMissingTypeStubs]
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from psycopg import sql
from sqlalchemy import Engine, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.errors import PROBLEM_CONTENT_TYPE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db import session as db_session
from sketch2cad.db.base import Base
from sketch2cad.db.models import Event, JobMeta, MediaType, Source, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.sources.ingest import (
    ALTERED_MESSAGE,
    INVALID_TYPE_MESSAGE,
    MAX_UPLOAD_BYTES,
    QC_TASK_NAME,
    TOO_LARGE_MESSAGE,
)
from sketch2cad.sources.magic import sniff_media_type
from sketch2cad.sources.upload import UploadTooLargeError, read_upload
from sketch2cad.worker.app import apply_schema, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
FILENAME_MARKER = "croquis-marker-7f3a9c.png"


@pytest.fixture(scope="module")
def sources_engine(database_url: str) -> Iterator[Engine]:
    """Own schema with the app tables and the Procrastinate schema; dropped afterwards."""
    schema = f"test_sources_{uuid.uuid4().hex}"
    admin = to_conninfo(database_url)
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    engine = create_db_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        apply_schema(to_conninfo(database_url, options=f"-csearch_path={schema}"))
        yield engine
    finally:
        engine.dispose()
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture
def factory(
    sources_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    session_factory = create_session_factory(sources_engine)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: session_factory)
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
def app(storage_root: Path, factory: sessionmaker[Session]) -> FastAPI:
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


def png_bytes(dpi: tuple[int, int] | None = (300, 300)) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("L", (64, 48), color=200)
    image.save(buffer, format="PNG", **({"dpi": dpi} if dpi else {}))
    return buffer.getvalue()


def jpeg_with_exif() -> bytes:
    image = Image.new("RGB", (40, 30), color=(10, 20, 30))
    exif = Image.Exif()
    exif[0x010F] = "ScannerCo"  # Make
    exif[0x0110] = "Model X"  # Model
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif, dpi=(200, 200))
    return buffer.getvalue()


def pdf_bytes(pages: int) -> bytes:
    document = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        document.new_page(595, 842)  # pyright: ignore[reportUnknownMemberType]
    buffer = io.BytesIO()
    document.save(buffer)  # pyright: ignore[reportUnknownMemberType]
    document.close()
    return buffer.getvalue()


def upload(
    client: TestClient,
    case_id: str,
    data: bytes,
    *,
    filename: str = "croquis.png",
    client_sha256: str | None = None,
) -> Any:
    digest = client_sha256 if client_sha256 is not None else hashlib.sha256(data).hexdigest()
    return client.post(
        f"{CASES_URL}/{case_id}/sources",
        files={"file": (filename, data, "application/octet-stream")},
        data={"client_sha256": digest},
    )


def stored_files(storage_root: Path) -> list[Path]:
    return [path for path in (storage_root / "objects").rglob("*") if path.is_file()]


def ingested_events(factory: sessionmaker[Session], case_id: str) -> list[Event]:
    with factory() as db:
        statement = select(Event).where(
            Event.case_file_id == uuid.UUID(case_id), Event.kind == EventKind.SOURCE_INGESTED
        )
        return list(db.scalars(statement).all())


def source_count(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return db.scalar(select(func.count()).select_from(Source)) or 0


def assert_problem(response: Any, status: int, detail: str | None = None) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    if detail is not None:
        assert response.json()["detail"] == detail


def assert_nothing_stored(factory: sessionmaker[Session], storage_root: Path, case_id: str) -> None:
    assert stored_files(storage_root) == []
    assert ingested_events(factory, case_id) == []


def test_upload_over_50_mb_returns_413(
    client: TestClient, case_id: str, factory: sessionmaker[Session], storage_root: Path
) -> None:
    data = b"\xff\xd8\xff" + b"\x00" * (MAX_UPLOAD_BYTES + 1 - 3)

    response = upload(client, case_id, data, filename="big.jpg")

    assert_problem(response, 413, TOO_LARGE_MESSAGE)
    assert_nothing_stored(factory, storage_root, case_id)


def test_jpg_with_text_content_returns_415(
    client: TestClient, case_id: str, factory: sessionmaker[Session], storage_root: Path
) -> None:
    response = upload(client, case_id, b"esto es texto, no una imagen\n", filename="croquis.jpg")

    assert_problem(response, 415, INVALID_TYPE_MESSAGE)
    assert_nothing_stored(factory, storage_root, case_id)


def test_two_page_pdf_returns_415(
    client: TestClient, case_id: str, factory: sessionmaker[Session], storage_root: Path
) -> None:
    response = upload(client, case_id, pdf_bytes(2), filename="plano.pdf")

    assert_problem(response, 415, INVALID_TYPE_MESSAGE)
    assert_nothing_stored(factory, storage_root, case_id)


def test_truncated_png_returns_415(client: TestClient, case_id: str) -> None:
    response = upload(client, case_id, png_bytes()[:12])

    assert_problem(response, 415, INVALID_TYPE_MESSAGE)


def test_mismatching_client_sha256_returns_422_and_stores_nothing(
    client: TestClient, case_id: str, factory: sessionmaker[Session], storage_root: Path
) -> None:
    data = png_bytes()

    response = upload(client, case_id, data, client_sha256=hashlib.sha256(b"other").hexdigest())

    assert_problem(response, 422, ALTERED_MESSAGE)
    assert stored_files(storage_root) == []
    assert not any((storage_root / "tmp").iterdir())
    assert source_count(factory) == 0


def test_valid_upload_returns_201_with_hash_event_and_queued_qc(
    client: TestClient, case_id: str, factory: sessionmaker[Session], storage_root: Path
) -> None:
    data = png_bytes()

    response = upload(client, case_id, data, client_sha256=hashlib.sha256(data).hexdigest().upper())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sha256"] == hashlib.sha256(data).hexdigest()
    assert body["media_type"] == MediaType.PNG.value
    assert (body["byte_size"], body["width_px"], body["height_px"]) == (len(data), 64, 48)
    assert body["dpi"] == pytest.approx(300, abs=0.1)
    assert body["qc"] is None
    [stored] = stored_files(storage_root)
    assert stored.read_bytes() == data
    events = ingested_events(factory, case_id)
    assert len(events) == 1
    assert events[0].entity_id == uuid.UUID(body["id"])
    assert events[0].after is not None and events[0].after["sha256"] == body["sha256"]
    with factory() as db:
        job = db.get(JobMeta, uuid.UUID(body["qc_job_id"]))
        assert job is not None
        assert (job.kind, job.status, job.source_id) == ("qc", "queued", uuid.UUID(body["id"]))
        task = db.execute(
            sql_text("SELECT task_name, args FROM procrastinate_jobs WHERE status = 'todo'")
        ).one()
    assert task.task_name == QC_TASK_NAME
    assert task.args == {"job_meta_id": body["qc_job_id"], "source_id": body["id"]}


def test_jpeg_exif_and_dpi_are_recorded(client: TestClient, case_id: str) -> None:
    response = upload(client, case_id, jpeg_with_exif(), filename="foto.jpg")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["media_type"] == MediaType.JPEG.value
    assert body["exif"]["Make"] == "ScannerCo"
    assert body["exif"]["Model"] == "Model X"
    assert body["dpi"] == pytest.approx(200, abs=0.1)


def test_single_page_pdf_is_accepted_without_raster_size(client: TestClient, case_id: str) -> None:
    response = upload(client, case_id, pdf_bytes(1), filename="plano.pdf")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["media_type"] == MediaType.PDF.value
    assert (body["width_px"], body["height_px"], body["dpi"]) == (None, None, None)


def test_missing_client_sha256_returns_422(client: TestClient, case_id: str) -> None:
    response = client.post(
        f"{CASES_URL}/{case_id}/sources", files={"file": ("a.png", png_bytes(), "image/png")}
    )

    assert_problem(response, 422)


def test_upload_requires_csrf_and_edit_permission(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
) -> None:
    reader = insert_user(factory)
    added = client.post(
        f"{CASES_URL}/{case_id}/members", json={"user_id": str(reader.id), "permissions": ["read"]}
    )
    assert added.status_code == 201, added.text
    reader_client = signed_in(app, stack, reader)

    assert_problem(upload(reader_client, case_id, png_bytes()), 403)
    del client.headers[CSRF_HEADER]
    assert_problem(upload(client, case_id, png_bytes()), 403)
    with factory() as db:
        denied = db.scalars(select(Event).where(Event.kind == EventKind.ACCESS_DENIED)).all()
    assert [event.actor_id for event in denied] == [reader.id]
    assert source_count(factory) == 0


def test_logs_exclude_file_name_and_exif(
    client: TestClient, case_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    data = jpeg_with_exif()
    with caplog.at_level(logging.DEBUG):
        response = upload(client, case_id, data, filename=FILENAME_MARKER)

    assert response.status_code == 201, response.text
    assert FILENAME_MARKER not in caplog.text
    assert "ScannerCo" not in caplog.text


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\xff\xd8\xff\xe0rest", MediaType.JPEG),
        (b"\x89PNG\r\n\x1a\nrest", MediaType.PNG),
        (b"II*\x00rest", MediaType.TIFF),
        (b"MM\x00*rest", MediaType.TIFF),
        (b"%PDF-1.7", MediaType.PDF),
        (b"GIF89a", None),
        (b"", None),
    ],
)
def test_sniff_media_type(head: bytes, expected: MediaType | None) -> None:
    assert sniff_media_type(head) is expected


def test_size_cap_is_enforced_while_streaming() -> None:
    boundary = "s2cboundary"
    consumed: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n\r\n'
        ).encode()
        for index in range(100):
            consumed.append(index)
            yield b"\x00" * 1024

    with pytest.raises(UploadTooLargeError):
        asyncio.run(
            read_upload(
                f"multipart/form-data; boundary={boundary}", body(), max_file_bytes=10 * 1024
            )
        )
    assert len(consumed) == 11
