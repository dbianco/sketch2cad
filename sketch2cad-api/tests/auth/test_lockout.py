"""Account lockout after 10 failed logins, driven by an injected clock."""

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import SESSION_COOKIE
from sketch2cad.api.errors import PROBLEM_CONTENT_TYPE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.api.routers import auth as auth_router
from sketch2cad.auth.lockout import (
    LOCKOUT_THRESHOLD,
    FailureResult,
    get_clock,
    register_failure,
)
from sketch2cad.auth.passwords import hash_password
from sketch2cad.config import get_settings
from sketch2cad.db.base import Base
from sketch2cad.db.models import User, UserRole

LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
WRONG_PASSWORD = "wrong password"  # noqa: S105 - test fixture
BASE_URL = "https://testserver"
LOCKOUT_MINUTES = 15
START = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
THREAD_TIMEOUT_SECONDS = 10


class FakeClock:
    """A controllable clock injected through the ``get_clock`` dependency."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


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
    monkeypatch.delenv("AUTH_LOCKOUT_MINUTES", raising=False)
    return monkeypatch


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def client(
    env: pytest.MonkeyPatch, factory: sessionmaker[Session], clock: FakeClock
) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_clock] = lambda: clock
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client


def insert_user(factory: sessionmaker[Session], is_active: bool = True) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name="Ana Pérez",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def load_user(factory: sessionmaker[Session], user_id: uuid.UUID) -> User:
    with factory() as db:
        return db.get_one(User, user_id)


def attempt(client: TestClient, username: str, password: str) -> int:
    response = client.post(LOGIN_URL, json={"username": username, "password": password})
    return response.status_code


def fail(client: TestClient, username: str, times: int) -> None:
    for _ in range(times):
        assert attempt(client, username, WRONG_PASSWORD) == 401


def test_lockout_defaults_to_15_minutes(env: pytest.MonkeyPatch) -> None:
    env.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5434/unused")

    assert get_settings().AUTH_LOCKOUT_MINUTES == LOCKOUT_MINUTES
    env.setenv("AUTH_LOCKOUT_MINUTES", "0")
    with pytest.raises(ValueError, match="AUTH_LOCKOUT_MINUTES"):
        get_settings()


def test_wrong_password_increments_failed_attempts(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)

    fail(client, user.username, 3)

    stored = load_user(factory, user.id)
    assert stored.failed_attempts == 3
    assert stored.locked_until is None


def test_eleventh_attempt_with_correct_password_returns_423(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    fail(client, user.username, LOCKOUT_THRESHOLD)

    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})

    assert response.status_code == 423
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    expected_until = START + timedelta(minutes=LOCKOUT_MINUTES)
    body = response.json()
    assert body["status"] == 423
    assert body["locked_until"] == expected_until.isoformat()
    assert datetime.fromisoformat(body["locked_until"]).utcoffset() == timedelta(0)
    assert SESSION_COOKIE not in response.cookies
    stored = load_user(factory, user.id)
    assert stored.failed_attempts == LOCKOUT_THRESHOLD
    assert stored.locked_until == expected_until


def test_locked_response_is_identical_for_correct_and_wrong_password(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    fail(client, user.username, LOCKOUT_THRESHOLD)

    correct = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})
    wrong = client.post(LOGIN_URL, json={"username": user.username, "password": WRONG_PASSWORD})

    assert correct.status_code == wrong.status_code == 423
    assert correct.content == wrong.content
    # Failures during an active lock neither count nor extend the lock.
    stored = load_user(factory, user.id)
    assert stored.failed_attempts == LOCKOUT_THRESHOLD
    assert stored.locked_until == START + timedelta(minutes=LOCKOUT_MINUTES)


def test_locked_user_still_verifies_the_password(
    client: TestClient, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = insert_user(factory)
    fail(client, user.username, LOCKOUT_THRESHOLD)
    calls: list[str] = []
    real_verify = auth_router.verify_password

    def spy(password_hash: str, password: str) -> bool:
        calls.append(password)
        return real_verify(password_hash, password)

    monkeypatch.setattr(auth_router, "verify_password", spy)

    assert attempt(client, user.username, PASSWORD) == 423
    assert calls == [PASSWORD]


def test_login_succeeds_after_lock_expires_and_resets_counter(
    client: TestClient, factory: sessionmaker[Session], clock: FakeClock
) -> None:
    user = insert_user(factory)
    fail(client, user.username, LOCKOUT_THRESHOLD)
    assert attempt(client, user.username, PASSWORD) == 423

    clock.advance(timedelta(minutes=16))
    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})

    assert response.status_code == 200
    assert SESSION_COOKIE in response.cookies
    stored = load_user(factory, user.id)
    assert stored.failed_attempts == 0
    assert stored.locked_until is None


def test_lock_ends_exactly_at_locked_until(
    client: TestClient, factory: sessionmaker[Session], clock: FakeClock
) -> None:
    user = insert_user(factory)
    fail(client, user.username, LOCKOUT_THRESHOLD)

    clock.advance(timedelta(minutes=LOCKOUT_MINUTES) - timedelta(microseconds=1))
    assert attempt(client, user.username, PASSWORD) == 423

    clock.advance(timedelta(microseconds=1))
    assert attempt(client, user.username, PASSWORD) == 200


def test_first_failure_after_expired_lock_starts_a_new_count(
    client: TestClient, factory: sessionmaker[Session], clock: FakeClock
) -> None:
    user = insert_user(factory)
    fail(client, user.username, LOCKOUT_THRESHOLD)
    clock.advance(timedelta(minutes=16))

    fail(client, user.username, 1)

    stored = load_user(factory, user.id)
    assert stored.failed_attempts == 1
    assert stored.locked_until is None
    assert attempt(client, user.username, PASSWORD) == 200


def test_lockout_duration_is_configurable(
    env: pytest.MonkeyPatch, client: TestClient, factory: sessionmaker[Session]
) -> None:
    env.setenv("AUTH_LOCKOUT_MINUTES", "30")
    user = insert_user(factory)
    fail(client, user.username, LOCKOUT_THRESHOLD)

    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})

    assert response.status_code == 423
    assert response.json()["locked_until"] == (START + timedelta(minutes=30)).isoformat()


def test_successful_login_before_threshold_resets_counter(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    fail(client, user.username, LOCKOUT_THRESHOLD - 1)
    assert load_user(factory, user.id).failed_attempts == LOCKOUT_THRESHOLD - 1

    assert attempt(client, user.username, PASSWORD) == 200

    assert load_user(factory, user.id).failed_attempts == 0
    # The counter starts over, so nine more failures still do not lock the account.
    fail(client, user.username, LOCKOUT_THRESHOLD - 1)
    assert attempt(client, user.username, PASSWORD) == 200


def test_unknown_user_never_gets_423(client: TestClient) -> None:
    username = f"nobody-{uuid.uuid4().hex[:8]}"
    first = client.post(LOGIN_URL, json={"username": username, "password": WRONG_PASSWORD})

    for _ in range(LOCKOUT_THRESHOLD):
        assert attempt(client, username, WRONG_PASSWORD) == 401
    last = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})

    assert last.status_code == 401
    assert last.content == first.content


def test_inactive_user_is_not_counted_and_keeps_the_401(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory, is_active=False)

    fail(client, user.username, LOCKOUT_THRESHOLD + 1)

    stored = load_user(factory, user.id)
    assert stored.failed_attempts == 0
    assert stored.locked_until is None


def test_concurrent_failures_are_not_lost(factory: sessionmaker[Session]) -> None:
    user = insert_user(factory)
    results: list[FailureResult] = []

    def second_failure() -> None:
        with factory() as db:
            results.append(register_failure(db, user.id, START, LOCKOUT_MINUTES))
            db.commit()

    with factory() as first:
        register_failure(first, user.id, START, LOCKOUT_MINUTES)
        # The second transaction blocks on the row lock held by the uncommitted first one.
        thread = threading.Thread(target=second_failure)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive()
        first.commit()
    thread.join(timeout=THREAD_TIMEOUT_SECONDS)

    assert not thread.is_alive()
    assert results == [FailureResult(failed_attempts=2, locked_until=None)]
    assert load_user(factory, user.id).failed_attempts == 2


def test_parallel_failures_reach_the_threshold_exactly(factory: sessionmaker[Session]) -> None:
    user = insert_user(factory)
    workers = 2
    per_worker = LOCKOUT_THRESHOLD // workers
    barrier = threading.Barrier(workers)

    def hammer() -> None:
        barrier.wait(timeout=THREAD_TIMEOUT_SECONDS)
        for _ in range(per_worker):
            with factory() as db:
                register_failure(db, user.id, START, LOCKOUT_MINUTES)
                db.commit()

    threads = [threading.Thread(target=hammer) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=THREAD_TIMEOUT_SECONDS)

    stored = load_user(factory, user.id)
    assert stored.failed_attempts == LOCKOUT_THRESHOLD
    assert stored.locked_until == START + timedelta(minutes=LOCKOUT_MINUTES)
