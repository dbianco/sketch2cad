"""Health endpoint and problem+json error handling."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.errors import PROBLEM_CONTENT_TYPE, ProblemError
from sketch2cad.api.routers import API_V1_PREFIX, api_v1_router
from sketch2cad.db.session import create_db_engine, create_session_factory, get_session

PROBLEM_KEYS = {"type", "title", "status", "detail"}
# Nothing listens on port 1; the connection is refused, as with a stopped database.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://sketch2cad:unused@127.0.0.1:1/sketch2cad"


class Item(BaseModel):
    name: str
    quantity: int


def _build_app() -> FastAPI:
    app = create_app()
    router = api_v1_router(tags=["test"])

    @router.post("/items")
    def create_item(item: Item) -> Item:  # pyright: ignore[reportUnusedFunction]
        return item

    @router.get("/conflict")
    def conflict() -> None:  # pyright: ignore[reportUnusedFunction]
        raise ProblemError(409, "Conflict", "The item already exists.", type="/problems/conflict")

    @router.get("/boom")
    def boom() -> None:  # pyright: ignore[reportUnusedFunction]
        raise RuntimeError("secret internal detail")

    app.include_router(router)
    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_build_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def _assert_problem(response_json: dict[str, object], status: int) -> None:
    assert PROBLEM_KEYS <= response_json.keys()
    assert response_json["status"] == status


def test_healthz_returns_ok_when_database_answers(
    client: TestClient, db_session_factory: sessionmaker[Session]
) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_healthz_returns_503_when_database_is_stopped(client: TestClient) -> None:
    engine = create_db_engine(UNREACHABLE_DATABASE_URL, connect_args={"connect_timeout": 1})
    factory = create_session_factory(engine)

    def unreachable_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[get_session] = unreachable_session
    try:
        response = client.get("/healthz")
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error"}


def test_healthz_is_not_under_api_v1(client: TestClient) -> None:
    response = client.get(f"{API_V1_PREFIX}/healthz")

    assert response.status_code == 404


def test_validation_error_returns_problem_json(client: TestClient) -> None:
    response = client.post(f"{API_V1_PREFIX}/items", json={"name": "x", "quantity": "many"})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = response.json()
    _assert_problem(body, 422)
    assert body["errors"][0]["loc"] == ["body", "quantity"]
    assert "many" not in response.text


def test_valid_request_reaches_the_versioned_router(client: TestClient) -> None:
    response = client.post(f"{API_V1_PREFIX}/items", json={"name": "x", "quantity": 2})

    assert response.status_code == 200
    assert response.json() == {"name": "x", "quantity": 2}


def test_problem_error_is_rendered_as_problem_json(client: TestClient) -> None:
    response = client.get(f"{API_V1_PREFIX}/conflict")

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json() == {
        "type": "/problems/conflict",
        "title": "Conflict",
        "status": 409,
        "detail": "The item already exists.",
    }


def test_unknown_route_returns_problem_json(client: TestClient) -> None:
    response = client.get(f"{API_V1_PREFIX}/missing")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    _assert_problem(response.json(), 404)


def test_unexpected_error_returns_500_without_internals(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = client.get(f"{API_V1_PREFIX}/boom")

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    _assert_problem(response.json(), 500)
    assert "secret internal detail" not in response.text
    assert "RuntimeError" in caplog.text
    assert "secret internal detail" not in caplog.text


def test_create_app_does_not_require_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "SESSION_SECRET", "SIGNING_KEY_PATH", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)

    app = create_app()

    assert "/healthz" in app.openapi()["paths"]
