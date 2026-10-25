"""auth.login and auth.failed events on the global auth chain (REQ-11, docs/audit.md)."""

import hashlib
import json
import re
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE, SESSION_COOKIE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind, append_event, event_content
from sketch2cad.audit.verify import VerifyResult, verify_chain
from sketch2cad.auth.lockout import LOCKOUT_THRESHOLD
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db.base import Base
from sketch2cad.db.models import Event, User, UserRole, UserSession

LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
WRONG_PASSWORD = "wrong horse battery staple"  # noqa: S105 - test fixture
BASE_URL = "https://testserver"
AUTH_GENESIS = hashlib.sha256(b"auth").hexdigest()
# The acceptance check of the task: `rg -n 'password|token'` over the serialized events.
FORBIDDEN = re.compile(r"password|token", re.IGNORECASE)
CONCURRENT_WRITERS = 2
EVENTS_PER_WRITER = 25
THREAD_TIMEOUT_SECONDS = 30

EventDict = dict[str, Any]


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
def client(monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    with TestClient(create_app(), base_url=BASE_URL) as test_client:
        yield test_client


def insert_user(factory: sessionmaker[Session], *, is_active: bool = True) -> User:
    with factory() as db:
        user = User(
            username=f"User-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def post_login(client: TestClient, username: str, password: str) -> int:
    response = client.post(LOGIN_URL, json={"username": username, "password": password})
    return response.status_code


def auth_chain(factory: sessionmaker[Session]) -> list[EventDict]:
    """The whole global auth chain, oldest first, as verifier dictionaries."""
    with factory() as db:
        statement = select(Event).where(Event.case_file_id.is_(None)).order_by(Event.id)
        return [event_content(row) | {"hash": row.hash} for row in db.scalars(statement)]


def last_event_id(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return db.scalar(select(func.coalesce(func.max(Event.id), 0))) or 0


def events_after(factory: sessionmaker[Session], event_id: int) -> list[EventDict]:
    return [event for event in auth_chain(factory) if event["id"] > event_id]


def serialized_lines(event: EventDict) -> list[str]:
    """The event serialized as indented JSON, split into the lines `rg -n` would scan."""
    return json.dumps(event, indent=2, ensure_ascii=False).splitlines()


def assert_no_secrets(events: list[EventDict], *secrets: str) -> None:
    for event in events:
        serialized = json.dumps(event)
        assert [line for line in serialized_lines(event) if FORBIDDEN.search(line)] == []
        for secret in secrets:
            assert secret not in serialized


def test_wrong_password_creates_one_auth_failed_event(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    before = last_event_id(factory)

    assert post_login(client, user.username, WRONG_PASSWORD) == 401

    (event,) = events_after(factory, before)
    assert event["kind"] == EventKind.AUTH_FAILED.value
    assert event["case_file_id"] is None
    assert event["actor_id"] == str(user.id)
    assert event["actor_role"] == "technician"
    assert event["client_ip"] == "testclient"
    assert event["after"] == {"cause": "invalid_credentials", "failed_attempts": 1}
    assert_no_secrets([event], WRONG_PASSWORD, PASSWORD)


def test_correct_password_creates_one_auth_login_event(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    before = last_event_id(factory)

    assert post_login(client, user.username, PASSWORD) == 200

    (event,) = events_after(factory, before)
    assert event["kind"] == EventKind.AUTH_LOGIN.value
    assert event["case_file_id"] is None
    assert event["actor_id"] == str(user.id)
    with factory() as db:
        record = db.scalars(select(UserSession).where(UserSession.user_id == user.id)).one()
    assert event["after"] == {"session_id": str(record.id)}
    assert_no_secrets(
        [event],
        PASSWORD,
        client.cookies[SESSION_COOKIE],
        client.cookies[CSRF_COOKIE],
        record.token_sha256,
    )


def test_unknown_user_creates_auth_failed_without_actor_or_username(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    username = f"Nobody-{uuid.uuid4().hex[:8]}"
    before = last_event_id(factory)

    response = client.post(LOGIN_URL, json={"username": username, "password": WRONG_PASSWORD})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."
    (event,) = events_after(factory, before)
    assert event["kind"] == EventKind.AUTH_FAILED.value
    assert event["actor_id"] is None
    assert event["actor_role"] is None
    expected_digest = hashlib.sha256(username.lower().encode("utf-8")).hexdigest()
    assert event["after"] == {"cause": "unknown_user", "username_sha256": expected_digest}
    assert_no_secrets([event], WRONG_PASSWORD, username, username.lower())


def test_inactive_user_creates_auth_failed(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory, is_active=False)
    before = last_event_id(factory)

    assert post_login(client, user.username, PASSWORD) == 401

    (event,) = events_after(factory, before)
    assert event["after"] == {"cause": "inactive_user"}
    assert event["actor_id"] == str(user.id)


def test_lockout_and_locked_attempts_are_recorded(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    before = last_event_id(factory)

    for _ in range(LOCKOUT_THRESHOLD):
        assert post_login(client, user.username, WRONG_PASSWORD) == 401
    assert post_login(client, user.username, PASSWORD) == 423

    events = events_after(factory, before)
    assert [event["kind"] for event in events] == [EventKind.AUTH_FAILED.value] * (
        LOCKOUT_THRESHOLD + 1
    )
    assert [event["after"]["failed_attempts"] for event in events[:-1]] == list(
        range(1, LOCKOUT_THRESHOLD + 1)
    )
    assert "locked_until" in events[LOCKOUT_THRESHOLD - 1]["after"]
    assert events[-1]["after"]["cause"] == "account_locked"
    assert (
        events[-1]["after"]["locked_until"]
        == events[LOCKOUT_THRESHOLD - 1]["after"]["locked_until"]
    )
    assert_no_secrets(events, WRONG_PASSWORD, PASSWORD)


def test_auth_event_stream_verifies_from_the_auth_genesis(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user = insert_user(factory)
    assert post_login(client, user.username, WRONG_PASSWORD) == 401
    assert post_login(client, user.username, PASSWORD) == 200
    assert post_login(client, f"ghost-{uuid.uuid4().hex[:8]}", PASSWORD) == 401

    events = auth_chain(factory)

    assert events[0]["prev_hash"] == AUTH_GENESIS
    assert verify_chain(events) == VerifyResult(valid=True, checked=len(events))
    assert_no_secrets(events, WRONG_PASSWORD, PASSWORD)
    events[-1]["after"]["cause"] = "invalid_credentials"
    assert verify_chain(events).first_invalid_index == len(events) - 1


def test_concurrent_global_appends_form_one_chain(factory: sessionmaker[Session]) -> None:
    barrier = threading.Barrier(CONCURRENT_WRITERS)
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            barrier.wait(timeout=THREAD_TIMEOUT_SECONDS)
            for _ in range(EVENTS_PER_WRITER):
                with factory.begin() as db:
                    append_event(
                        db,
                        case_file_id=None,
                        kind=EventKind.AUTH_FAILED,
                        after={"cause": "unknown_user"},
                    )
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(CONCURRENT_WRITERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=THREAD_TIMEOUT_SECONDS)

    assert errors == []
    events = auth_chain(factory)
    assert len(events) >= CONCURRENT_WRITERS * EVENTS_PER_WRITER
    assert verify_chain(events) == VerifyResult(valid=True, checked=len(events))
