"""``GET /api/v1/catalog/active``: any authenticated user reads the active catalog."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.errors import PROBLEM_CONTENT_TYPE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.auth.passwords import hash_password
from sketch2cad.catalog import catalog_json, default_catalog
from sketch2cad.db.base import Base
from sketch2cad.db.models import Catalog, User, UserRole

LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
ACTIVE_URL = f"{API_V1_PREFIX}/catalog/active"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
BASE_URL = "https://testserver"


@pytest.fixture(scope="module")
def tables(db_engine: Engine) -> Iterator[None]:
    """Create the application tables in the isolated schema and drop them afterwards."""
    # Pooled connections may hold prepared plans for tables another module dropped.
    db_engine.dispose()
    Base.metadata.create_all(db_engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.fixture
def factory(
    tables: None, db_session_factory: sessionmaker[Session]
) -> Iterator[sessionmaker[Session]]:
    yield db_session_factory
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with db_session_factory() as db:
        db.execute(text(f"TRUNCATE {names} CASCADE"))
        db.commit()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]) -> FastAPI:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    return create_app()


@pytest.fixture
def client(app: FastAPI, factory: sessionmaker[Session]) -> Iterator[TestClient]:
    """A technician with no case membership at all, signed in."""
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
        )
        db.add(user)
        db.commit()
        username = user.username
    with TestClient(app, base_url=BASE_URL) as test_client:
        response = test_client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
        assert response.status_code == 200
        yield test_client


def insert_catalog(factory: sessionmaker[Session], version: str, *, active: bool) -> uuid.UUID:
    data = catalog_json(default_catalog())
    with factory() as db:
        catalog = Catalog(
            version=version, is_active=active, layers=data["layers"], symbols=data["symbols"]
        )
        db.add(catalog)
        db.commit()
        return catalog.id


def assert_problem(response: Any, status: int) -> None:
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


def test_returns_the_active_catalog_with_inline_svg(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    insert_catalog(factory, "old-0", active=False)
    catalog_id = insert_catalog(factory, "default-1", active=True)

    response = client.get(ACTIVE_URL)

    assert response.status_code == 200
    body = response.json()
    expected = json.loads(json.dumps(catalog_json(default_catalog())))
    assert body == {"id": str(catalog_id), **expected}
    assert len(body["layers"]) == 10
    assert all(symbol["svg"].startswith("<svg") for symbol in body["symbols"])


def test_no_active_catalog_is_404(client: TestClient, factory: sessionmaker[Session]) -> None:
    insert_catalog(factory, "default-1", active=False)

    assert_problem(client.get(ACTIVE_URL), 404)


def test_requires_a_session(app: FastAPI, factory: sessionmaker[Session]) -> None:
    insert_catalog(factory, "default-1", active=True)

    with TestClient(app, base_url=BASE_URL) as anonymous:
        assert_problem(anonymous.get(ACTIVE_URL), 401)
