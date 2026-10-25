"""Original download (``source.downloaded``) and derived JPEG previews of sources."""

import hashlib
import io
import os
import uuid
from collections.abc import Iterator
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
from sqlalchemy import Engine, select
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
from sketch2cad.db.models import Event, MediaType, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.sources.preview import render_preview
from sketch2cad.worker.app import apply_schema, to_conninfo

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
SOURCES_URL = f"{API_V1_PREFIX}/sources"


@pytest.fixture(scope="module")
def sources_engine(database_url: str) -> Iterator[Engine]:
    """Own schema with the app tables and the Procrastinate schema; dropped afterwards."""
    schema = f"test_download_{uuid.uuid4().hex}"
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
def owner(factory: sessionmaker[Session]) -> User:
    return insert_user(factory)


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    return signed_in(app, stack, owner)


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


def noise_png(size: tuple[int, int]) -> bytes:
    """Incompressible PNG, so large sizes give files spanning several storage chunks."""
    image = Image.frombytes("L", size, os.urandom(size[0] * size[1]))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_with_exif(size: tuple[int, int]) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "ScannerCo"  # Make
    exif[0x0112] = 6  # Orientation
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(10, 20, 30)).save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def a4_pdf() -> bytes:
    document = pypdfium2.PdfDocument.new()
    document.new_page(595, 842)  # pyright: ignore[reportUnknownMemberType]
    buffer = io.BytesIO()
    document.save(buffer)  # pyright: ignore[reportUnknownMemberType]
    document.close()
    return buffer.getvalue()


def upload(client: TestClient, case_id: str, data: bytes, filename: str = "croquis.png") -> str:
    response = client.post(
        f"{CASES_URL}/{case_id}/sources",
        files={"file": (filename, data, "application/octet-stream")},
        data={"client_sha256": hashlib.sha256(data).hexdigest()},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def events(factory: sessionmaker[Session], kind: EventKind) -> list[Event]:
    with factory() as db:
        statement = select(Event).where(Event.kind == kind.value).order_by(Event.id)
        return list(db.scalars(statement).all())


def assert_problem(response: Any, status: int) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


def jpeg_of(response: Any) -> Image.Image:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "JPEG"
    return image


def test_download_returns_the_registered_bytes_hash_and_event(
    client: TestClient, owner: User, case_id: str, factory: sessionmaker[Session]
) -> None:
    data = noise_png((1800, 1800))  # about 3 MB: several chunks of the stream
    source_id = upload(client, case_id, data, filename="croquis ñandú.png")

    response = client.get(f"{SOURCES_URL}/{source_id}/original")

    assert response.status_code == 200, response.text
    digest = hashlib.sha256(response.content).hexdigest()
    assert response.content == data
    assert digest == hashlib.sha256(data).hexdigest()
    assert response.headers["X-Content-SHA256"] == digest
    assert response.headers["content-type"] == MediaType.PNG.value
    assert response.headers["content-length"] == str(len(data))
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="croquis _and_.png"')
    assert "filename*=UTF-8''croquis%20%C3%B1and%C3%BA.png" in disposition
    [downloaded] = events(factory, EventKind.SOURCE_DOWNLOADED)
    assert downloaded.case_file_id == uuid.UUID(case_id)
    assert downloaded.entity_id == uuid.UUID(source_id)
    assert (downloaded.actor_id, downloaded.actor_role) == (owner.id, "technician")
    assert downloaded.after == {"sha256": digest, "byte_size": len(data)}


def test_preview_fits_max_and_rejects_out_of_range_values(
    client: TestClient, case_id: str, factory: sessionmaker[Session]
) -> None:
    source_id = upload(client, case_id, noise_png((1500, 600)))
    url = f"{SOURCES_URL}/{source_id}/preview"

    assert max(jpeg_of(client.get(url, params={"max": 512})).size) <= 512
    assert jpeg_of(client.get(url, params={"max": 256})).size == (256, 102)
    assert jpeg_of(client.get(url)).size == (1500, 600)  # default 2048: never enlarged
    assert_problem(client.get(url, params={"max": 100}), 422)
    assert_problem(client.get(url, params={"max": 4097}), 422)
    assert_problem(client.get(url, params={"max": "big"}), 422)
    assert events(factory, EventKind.SOURCE_DOWNLOADED) == []


def test_preview_drops_exif_and_keeps_the_stored_orientation(
    client: TestClient, case_id: str
) -> None:
    source_id = upload(client, case_id, jpeg_with_exif((900, 300)), filename="foto.jpg")

    image = jpeg_of(client.get(f"{SOURCES_URL}/{source_id}/preview", params={"max": 300}))

    assert image.size == (300, 100)
    assert dict(image.getexif()) == {}
    assert "exif" not in image.info


def test_pdf_preview_renders_the_first_page(client: TestClient, case_id: str) -> None:
    source_id = upload(client, case_id, a4_pdf(), filename="plano.pdf")

    image = jpeg_of(client.get(f"{SOURCES_URL}/{source_id}/preview", params={"max": 512}))

    width, height = image.size
    assert height <= 512
    assert height >= 510
    assert width == pytest.approx(512 * 595 / 842, abs=2)


def test_non_member_gets_403_and_access_denied_on_both_routes(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
) -> None:
    source_id = upload(client, case_id, noise_png((64, 48)))
    outsider = insert_user(factory)
    outsider_client = signed_in(app, stack, outsider)

    assert_problem(outsider_client.get(f"{SOURCES_URL}/{source_id}/original"), 403)
    assert_problem(outsider_client.get(f"{SOURCES_URL}/{source_id}/preview"), 403)

    denied = events(factory, EventKind.ACCESS_DENIED)
    assert [(e.case_file_id, e.actor_id) for e in denied] == [(uuid.UUID(case_id), outsider.id)] * 2
    assert [e.after for e in denied] == [
        {
            "method": "GET",
            "route": f"{SOURCES_URL}/{{source_id}}/{name}",
            "permission": "read",
            "status": 403,
        }
        for name in ("original", "preview")
    ]
    assert events(factory, EventKind.SOURCE_DOWNLOADED) == []


def test_read_member_may_download_and_preview(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    case_id: str,
    factory: sessionmaker[Session],
) -> None:
    data = noise_png((64, 48))
    source_id = upload(client, case_id, data)
    reader = insert_user(factory)
    added = client.post(
        f"{CASES_URL}/{case_id}/members", json={"user_id": str(reader.id), "permissions": ["read"]}
    )
    assert added.status_code == 201, added.text
    reader_client = signed_in(app, stack, reader)

    assert reader_client.get(f"{SOURCES_URL}/{source_id}/original").content == data
    assert jpeg_of(reader_client.get(f"{SOURCES_URL}/{source_id}/preview")).size == (64, 48)
    assert [e.actor_id for e in events(factory, EventKind.SOURCE_DOWNLOADED)] == [reader.id]
    assert events(factory, EventKind.ACCESS_DENIED) == []


@pytest.mark.parametrize("route", ["original", "preview"])
def test_missing_or_malformed_source_is_404_without_events(
    client: TestClient, case_id: str, factory: sessionmaker[Session], route: str
) -> None:
    assert_problem(client.get(f"{SOURCES_URL}/{uuid.uuid4()}/{route}"), 404)
    assert_problem(client.get(f"{SOURCES_URL}/not-a-uuid/{route}"), 404)
    assert events(factory, EventKind.ACCESS_DENIED) == []


def test_anonymous_request_gets_401(app: FastAPI, stack: ExitStack) -> None:
    anonymous = stack.enter_context(TestClient(app, base_url="https://testserver"))

    assert_problem(anonymous.get(f"{SOURCES_URL}/{uuid.uuid4()}/original"), 401)
    assert_problem(anonymous.get(f"{SOURCES_URL}/{uuid.uuid4()}/preview"), 401)


@pytest.mark.parametrize("mode", ["1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I;16"])
def test_render_preview_handles_every_pixel_mode(mode: str) -> None:
    buffer = io.BytesIO()
    Image.new(mode, (1000, 400)).save(buffer, format="TIFF")
    buffer.seek(0)

    image = Image.open(io.BytesIO(render_preview(buffer, MediaType.TIFF, 500)))

    assert image.format == "JPEG"
    assert image.size == (500, 200)
    assert image.mode in ("L", "RGB")
