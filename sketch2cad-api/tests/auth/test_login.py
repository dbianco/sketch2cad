"""Password hashing, sessions and the login, logout and me endpoints."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE, SESSION_COOKIE
from sketch2cad.api.errors import PROBLEM_CONTENT_TYPE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.api.routers import auth as auth_router
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password, needs_rehash, verify_password
from sketch2cad.auth.sessions import hash_token
from sketch2cad.db.base import Base
from sketch2cad.db.models import User, UserRole, UserSession

LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
LOGOUT_URL = f"{API_V1_PREFIX}/auth/logout"
ME_URL = f"{API_V1_PREFIX}/auth/me"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
# Secure cookies are only sent back over HTTPS, so the client talks to an https origin.
BASE_URL = "https://testserver"
# Weaker than the argon2-cffi defaults, so its hashes must be upgraded on login.
WEAK_HASHER = PasswordHasher(time_cost=1, memory_cost=64, parallelism=1)


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


@pytest.fixture
def client(env: pytest.MonkeyPatch, factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app(), base_url=BASE_URL) as test_client:
        yield test_client


def insert_user(
    factory: sessionmaker[Session],
    password: str = PASSWORD,
    is_active: bool = True,
    password_hash: str | None = None,
) -> User:
    """Insert a user directly (the create-user CLI does not exist yet)."""
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name="Ana Pérez",
            password_hash=password_hash or hash_password(password),
            role=UserRole.TECHNICIAN,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def login(client: TestClient, username: str, password: str = PASSWORD) -> str:
    response = client.post(LOGIN_URL, json={"username": username, "password": password})
    assert response.status_code == 200
    return response.cookies[SESSION_COOKIE]


def set_cookie_header(headers: list[str], name: str) -> str:
    matches = [header for header in headers if header.startswith(f"{name}=")]
    assert len(matches) == 1
    return matches[0]


def find_session(factory: sessionmaker[Session], token: str) -> UserSession:
    with factory() as db:
        statement = select(UserSession).where(UserSession.token_sha256 == hash_token(token))
        return db.scalars(statement).one()


def assert_unauthorized(client: TestClient) -> None:
    response = client.get(ME_URL)
    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["status"] == 401


def use_token(client: TestClient, token: str) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, token)


def test_hash_password_uses_argon2id_and_verifies() -> None:
    password_hash = hash_password(PASSWORD)

    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, PASSWORD)
    assert not verify_password(password_hash, "wrong")
    assert not verify_password("not-a-hash", PASSWORD)
    assert not needs_rehash(password_hash)
    assert needs_rehash(WEAK_HASHER.hash(PASSWORD))


def test_login_sets_cookies_and_me_returns_the_user(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)

    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})

    assert response.status_code == 200
    expected = {
        "user": {
            "id": str(user.id),
            "username": user.username,
            "display_name": "Ana Pérez",
            "role": "technician",
        }
    }
    assert response.json() == expected
    headers = response.headers.get_list("set-cookie")
    session_cookie = set_cookie_header(headers, SESSION_COOKIE)
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "Secure" in session_cookie
    assert "Path=/" in session_cookie
    csrf_cookie = set_cookie_header(headers, CSRF_COOKIE)
    assert "HttpOnly" not in csrf_cookie
    assert "samesite=lax" in csrf_cookie.lower()
    assert "Secure" in csrf_cookie

    me = client.get(ME_URL)
    assert me.status_code == 200
    assert me.json() == expected


def test_cookies_are_not_secure_in_dev(
    env: pytest.MonkeyPatch, client: TestClient, factory: sessionmaker[Session]
) -> None:
    env.setenv("APP_ENV", "dev")
    user = insert_user(factory)

    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})

    assert response.status_code == 200
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 2
    for header in headers:
        assert "Secure" not in header


def test_unknown_user_and_wrong_password_return_identical_401(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)

    wrong_password = client.post(LOGIN_URL, json={"username": user.username, "password": "nope"})
    unknown_user = client.post(LOGIN_URL, json={"username": "nobody", "password": "nope"})

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert wrong_password.content == unknown_user.content
    assert wrong_password.json()["detail"] == "Invalid username or password."
    assert "set-cookie" not in wrong_password.headers
    assert "set-cookie" not in unknown_user.headers


def test_unknown_user_still_verifies_a_password_hash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    real_verify = auth_router.verify_password

    def spy(password_hash: str, password: str) -> bool:
        calls.append((password_hash, password))
        return real_verify(password_hash, password)

    monkeypatch.setattr("sketch2cad.auth.passwords.verify_password", spy)

    response = client.post(LOGIN_URL, json={"username": "nobody", "password": "guess"})

    assert response.status_code == 401
    assert len(calls) == 1
    assert calls[0][0].startswith("$argon2id$")
    assert calls[0][1] == "guess"


def test_inactive_user_gets_the_same_401_as_wrong_password(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    inactive = insert_user(factory, is_active=False)

    response = client.post(LOGIN_URL, json={"username": inactive.username, "password": PASSWORD})
    unknown = client.post(LOGIN_URL, json={"username": "nobody", "password": PASSWORD})

    assert response.status_code == 401
    assert response.content == unknown.content


def test_database_never_stores_the_raw_session_token(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)

    token = login(client, user.username)

    count_sql = text("SELECT count(*) FROM session WHERE token_sha256 = :value")
    with factory() as db:
        assert db.execute(count_sql, {"value": token}).scalar_one() == 0
        assert db.execute(count_sql, {"value": hash_token(token)}).scalar_one() == 1
    record = find_session(factory, token)
    assert record.csrf_token == client.cookies[CSRF_COOKIE]
    assert record.csrf_token != token


def test_session_expires_after_default_ttl(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    before = datetime.now(UTC)

    record = find_session(factory, login(client, user.username))

    lifetime = record.expires_at - before
    assert timedelta(minutes=480) <= lifetime < timedelta(minutes=481)


def test_session_ttl_is_configurable(
    env: pytest.MonkeyPatch, client: TestClient, factory: sessionmaker[Session]
) -> None:
    env.setenv("AUTH_SESSION_TTL_MINUTES", "30")
    user = insert_user(factory)
    before = datetime.now(UTC)

    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})

    record = find_session(factory, response.cookies[SESSION_COOKIE])
    assert timedelta(minutes=30) <= record.expires_at - before < timedelta(minutes=31)
    assert "Max-Age=1800" in set_cookie_header(
        response.headers.get_list("set-cookie"), SESSION_COOKIE
    )


def test_logout_revokes_the_session_and_clears_cookies(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    token = login(client, user.username)

    response = client.post(LOGOUT_URL, headers={CSRF_HEADER: client.cookies[CSRF_COOKIE]})

    assert response.status_code == 204
    assert find_session(factory, token).revoked_at is not None
    headers = response.headers.get_list("set-cookie")
    assert "Max-Age=0" in set_cookie_header(headers, SESSION_COOKIE)
    assert "Max-Age=0" in set_cookie_header(headers, CSRF_COOKIE)
    assert_unauthorized(client)
    # Replaying the raw token after logout is rejected too, not just the cleared cookie.
    use_token(client, token)
    assert_unauthorized(client)


def test_logout_without_session_returns_401(client: TestClient) -> None:
    response = client.post(LOGOUT_URL)

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


def test_expired_session_is_rejected(client: TestClient, factory: sessionmaker[Session]) -> None:
    user = insert_user(factory)
    login(client, user.username)
    with factory() as db:
        db.execute(
            text("UPDATE session SET expires_at = now() - interval '1 second' WHERE user_id = :id"),
            {"id": user.id},
        )
        db.commit()

    assert_unauthorized(client)


def test_revoked_session_is_rejected(client: TestClient, factory: sessionmaker[Session]) -> None:
    user = insert_user(factory)
    token = login(client, user.username)
    with factory() as db:
        db.execute(
            text("UPDATE session SET revoked_at = now() WHERE token_sha256 = :hash"),
            {"hash": hash_token(token)},
        )
        db.commit()

    assert_unauthorized(client)


def test_me_rejects_missing_or_unknown_token(client: TestClient) -> None:
    assert_unauthorized(client)

    use_token(client, "not-a-real-token")
    assert_unauthorized(client)


def test_me_rejects_session_of_deactivated_user(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    login(client, user.username)
    with factory() as db:
        db.execute(text('UPDATE "user" SET is_active = false WHERE id = :id'), {"id": user.id})
        db.commit()

    assert_unauthorized(client)


def test_login_rehashes_outdated_password_hash(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    weak_hash = WEAK_HASHER.hash(PASSWORD)
    user = insert_user(factory, password_hash=weak_hash)

    login(client, user.username)

    with factory() as db:
        stored = db.get_one(User, user.id).password_hash
    assert stored != weak_hash
    assert not needs_rehash(stored)
    assert verify_password(stored, PASSWORD)
