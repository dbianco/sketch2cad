"""CSRF enforcement on mutations and the session cookie flags (decision D-8, H0-13)."""

import uuid
from collections.abc import Callable, Iterator
from typing import Any, Protocol, cast

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE, SESSION_COOKIE, require_csrf
from sketch2cad.api.errors import PROBLEM_CONTENT_TYPE, register_error_handlers
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.api.routers import auth as auth_router
from sketch2cad.auth.csrf import CSRF_HEADER, MUTATING_METHODS, csrf_tokens_match
from sketch2cad.auth.passwords import hash_password
from sketch2cad.auth.sessions import hash_token
from sketch2cad.db.base import Base
from sketch2cad.db.models import User, UserRole, UserSession

LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
LOGOUT_URL = f"{API_V1_PREFIX}/auth/logout"
ME_URL = f"{API_V1_PREFIX}/auth/me"
PROBE_URL = "/csrf-probe"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
BASE_URL = "https://testserver"
VERBS = ["POST", "PUT", "PATCH", "DELETE"]
# Mutation routes that may run without a CSRF token: login has no session yet.
CSRF_EXEMPT_ROUTES = {("POST", LOGIN_URL)}


@pytest.fixture(scope="module")
def tables(db_engine: Engine) -> Iterator[None]:
    """Create the application tables in the isolated schema and drop them afterwards."""
    Base.metadata.create_all(db_engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.fixture
def factory(tables: None, db_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return db_session_factory


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AUTH_SESSION_TTL_MINUTES", raising=False)
    return monkeypatch


def probe_app() -> FastAPI:
    """A fresh app with the auth routes plus a test-only route per verb, all CSRF-protected."""
    router = APIRouter(dependencies=[Depends(require_csrf)])

    def ok() -> dict[str, bool]:
        return {"ok": True}

    router.add_api_route(PROBE_URL, ok, methods=[*VERBS, "GET"])
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_router.router)
    app.include_router(router)
    return app


@pytest.fixture
def client(env: pytest.MonkeyPatch, factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(probe_app(), base_url=BASE_URL) as test_client:
        yield test_client


def insert_user(factory: sessionmaker[Session]) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name="Ana Pérez",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def sign_in(client: TestClient, factory: sessionmaker[Session]) -> str:
    """Log a fresh user in and return the CSRF token the server issued."""
    user = insert_user(factory)
    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    return client.cookies[CSRF_COOKIE]


def session_cookie_header(client: TestClient, factory: sessionmaker[Session]) -> str:
    user = insert_user(factory)
    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    headers = response.headers.get_list("set-cookie")
    matches = [header for header in headers if header.startswith(f"{SESSION_COOKIE}=")]
    assert len(matches) == 1
    return matches[0]


def assert_forbidden(response: Any, token: str) -> None:
    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = response.json()
    assert body["status"] == 403
    assert body["detail"] == "A valid CSRF token is required."
    assert token not in response.text


@pytest.mark.parametrize("method", VERBS)
def test_mutation_without_token_returns_403(
    client: TestClient, factory: sessionmaker[Session], method: str
) -> None:
    token = sign_in(client, factory)

    response = client.request(method, PROBE_URL)

    assert_forbidden(response, token)


@pytest.mark.parametrize("method", VERBS)
def test_mutation_with_wrong_token_returns_403(
    client: TestClient, factory: sessionmaker[Session], method: str
) -> None:
    token = sign_in(client, factory)

    response = client.request(method, PROBE_URL, headers={CSRF_HEADER: "not-the-token"})

    assert_forbidden(response, token)


@pytest.mark.parametrize("method", VERBS)
def test_mutation_with_right_token_passes(
    client: TestClient, factory: sessionmaker[Session], method: str
) -> None:
    token = sign_in(client, factory)

    response = client.request(method, PROBE_URL, headers={CSRF_HEADER: token})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("method", VERBS)
def test_token_matching_a_forged_cookie_but_not_the_session_returns_403(
    client: TestClient, factory: sessionmaker[Session], method: str
) -> None:
    token = sign_in(client, factory)
    forged = "attacker-chosen-value"
    client.cookies.set(CSRF_COOKIE, forged)

    response = client.request(method, PROBE_URL, headers={CSRF_HEADER: forged})

    assert_forbidden(response, token)


def test_token_without_csrf_cookie_returns_403(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    token = sign_in(client, factory)
    session_token = client.cookies[SESSION_COOKIE]
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, session_token)

    response = client.post(PROBE_URL, headers={CSRF_HEADER: token})

    assert_forbidden(response, token)


def test_mutation_without_session_returns_401_before_csrf(client: TestClient) -> None:
    response = client.post(PROBE_URL)

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


def test_safe_method_is_not_checked_even_on_a_protected_route(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    sign_in(client, factory)

    assert client.get(PROBE_URL).status_code == 200


def test_me_without_token_returns_200(
    env: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> None:
    with TestClient(create_app(), base_url=BASE_URL) as real_client:
        sign_in(real_client, factory)

        response = real_client.get(ME_URL)

    assert response.status_code == 200


def test_logout_without_token_returns_403_and_keeps_the_session(
    env: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> None:
    with TestClient(create_app(), base_url=BASE_URL) as real_client:
        token = sign_in(real_client, factory)
        session_token = real_client.cookies[SESSION_COOKIE]

        response = real_client.post(LOGOUT_URL)

        assert_forbidden(response, token)
        assert real_client.get(ME_URL).status_code == 200
    with factory() as db:
        statement = select(UserSession).where(UserSession.token_sha256 == hash_token(session_token))
        assert db.scalars(statement).one().revoked_at is None


def test_session_cookie_is_httponly_and_samesite_lax(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    header = session_cookie_header(client, factory)

    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()


def test_session_cookie_is_secure_in_prod(
    env: pytest.MonkeyPatch, client: TestClient, factory: sessionmaker[Session]
) -> None:
    env.setenv("APP_ENV", "prod")

    header = session_cookie_header(client, factory)

    assert "secure" in [part.strip().lower() for part in header.split(";")]
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()


def test_csrf_tokens_match_requires_every_value() -> None:
    assert csrf_tokens_match("abc", "abc", "abc")
    assert not csrf_tokens_match(None, "abc", "abc")
    assert not csrf_tokens_match("", "", "")
    assert not csrf_tokens_match("abc", None, "abc")
    assert not csrf_tokens_match("abc", "abc", "abd")
    assert not csrf_tokens_match("abc", "abd", "abc")
    assert not csrf_tokens_match("é", "é", "e")


class RouteView(Protocol):
    """What the guard needs from a route: its path, methods and effective dependency tree."""

    @property
    def path(self) -> str: ...

    @property
    def methods(self) -> set[str] | None: ...

    @property
    def dependant(self) -> Dependant: ...


def effective_routes(app: FastAPI) -> Iterator[RouteView]:
    """Yield every API route of ``app``, including those of included routers.

    FastAPI 0.16x keeps included routers as lazy entries in ``app.routes`` that expose
    ``effective_route_contexts()``; each context carries the merged path, methods and
    dependant (router-level dependencies included).
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            yield from cast(Iterator[RouteView], contexts())


def _calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from _calls(dependency)


def mutations(app: FastAPI) -> dict[tuple[str, str], bool]:
    """Map each mutating ``(method, path)`` of ``app`` to whether it depends on require_csrf."""
    found: dict[tuple[str, str], bool] = {}
    for route in effective_routes(app):
        protected = require_csrf in set(_calls(route.dependant))
        for method in (route.methods or set()) & MUTATING_METHODS:
            found[(method, route.path)] = protected
    return found


def unprotected_mutations(app: FastAPI) -> list[tuple[str, str]]:
    """Return the mutating ``(method, path)`` pairs of ``app`` lacking ``require_csrf``."""
    return sorted(
        key
        for key, protected in mutations(app).items()
        if not protected and key not in CSRF_EXEMPT_ROUTES
    )


def openapi_mutations(app: FastAPI) -> set[tuple[str, str]]:
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    return {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() in MUTATING_METHODS
    }


def test_every_mutation_route_requires_csrf() -> None:
    """Guard for future routers: any new mutating route must depend on ``require_csrf``."""
    app = create_app()

    assert unprotected_mutations(app) == []
    # The walk must see every documented mutation, or the guard above would pass vacuously.
    assert openapi_mutations(app) <= set(mutations(app))
    assert {("POST", LOGIN_URL), ("POST", LOGOUT_URL)} <= set(mutations(app))


def test_route_guard_detects_an_unprotected_mutation() -> None:
    def unsafe() -> None:
        return None

    app = probe_app()
    app.add_api_route("/unsafe-direct", unsafe, methods=["PATCH", "GET"])
    included = APIRouter(prefix="/nested")
    included.add_api_route("/unsafe", unsafe, methods=["DELETE"])
    app.include_router(included)

    assert unprotected_mutations(app) == [("DELETE", "/nested/unsafe"), ("PATCH", "/unsafe-direct")]
    assert mutations(app)[("PUT", PROBE_URL)] is True
