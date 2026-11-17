"""Event log endpoint: id-ordered pages of 50, filters, access control and re-verification."""

import base64
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.errors import PROBLEM_CONTENT_TYPE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.api.routers.events import PAGE_SIZE, decode_cursor, encode_cursor
from sketch2cad.audit import EventKind, append_event
from sketch2cad.audit.verify import EVENT_FIELDS, InvalidReason, verify_chain
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db.base import Base
from sketch2cad.db.models import Event, User, UserRole

LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
CASES_URL = f"{API_V1_PREFIX}/case-files"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
BASE_URL = "https://testserver"
# Whole second on purpose: the hashed text keeps ``.000000`` where isoformat would not.
BASE_TIME = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
EXTRA_EVENTS = 119
PLAN_ID = uuid.uuid4()
OTHER_PLAN_ID = uuid.uuid4()


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
        db.refresh(user)
        return user


def signed_in(app: FastAPI, stack: ExitStack, user: User) -> TestClient:
    client = stack.enter_context(TestClient(app, base_url=BASE_URL))
    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def owner(factory: sessionmaker[Session]) -> User:
    return insert_user(factory)


@pytest.fixture
def other(factory: sessionmaker[Session]) -> User:
    return insert_user(factory)


@pytest.fixture
def owner_client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    return signed_in(app, stack, owner)


@pytest.fixture
def other_client(app: FastAPI, stack: ExitStack, other: User) -> TestClient:
    return signed_in(app, stack, other)


@pytest.fixture
def case_id(owner_client: TestClient) -> str:
    response = owner_client.post(
        CASES_URL,
        json={
            "incident_type": "traffic_collision",
            "incident_date": "2026-10-20",
            "description": "Colisión entre dos vehículos",
            "declared_location": "Av. Libertador 1200",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def kind_for(index: int) -> EventKind:
    if index % 3 == 0:
        return EventKind.SOURCE_INGESTED
    if index % 3 == 1:
        return EventKind.SOURCE_DOWNLOADED
    return EventKind.PLAN_CREATED


@pytest.fixture
def populated(factory: sessionmaker[Session], case_id: str, owner: User) -> str:
    """The case file with 1 + 119 = 120 events: 40 ingested, 40 downloaded, 39 plan."""
    with factory() as db:
        for index in range(EXTRA_EVENTS):
            kind = kind_for(index)
            append_event(
                db,
                case_file_id=uuid.UUID(case_id),
                kind=kind,
                actor_id=owner.id,
                actor_role=owner.role.value,
                plan_id=(PLAN_ID if index % 2 == 0 else OTHER_PLAN_ID)
                if kind is EventKind.PLAN_CREATED
                else None,
                entity_id=uuid.uuid4(),
                after={"index": index, "scale": 1.5, "nested": {"ñandú": [1, None, True]}},
                client_ip="203.0.113.7",
                occurred_at=BASE_TIME + timedelta(seconds=index),
            )
        db.commit()
    return case_id


def events_url(case_id: str) -> str:
    return f"{CASES_URL}/{case_id}/events"


def fetch_all(client: TestClient, case_id: str, **params: str) -> list[dict[str, Any]]:
    """Follow ``next_cursor`` until the last page; every page but the last is full."""
    pages: list[list[dict[str, Any]]] = []
    cursor: str | None = None
    while True:
        query = {**params, **({"cursor": cursor} if cursor is not None else {})}
        response = client.get(events_url(case_id), params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert len(body["items"]) == PAGE_SIZE
    return [item for page in pages for item in page]


def stored(factory: sessionmaker[Session], case_id: str) -> list[Event]:
    with factory() as db:
        statement = select(Event).where(Event.case_file_id == uuid.UUID(case_id))
        return list(db.scalars(statement.order_by(Event.id)).all())


def assert_problem(response: Any, status: int) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


def test_pages_of_50_in_id_order_with_next_cursor(
    owner_client: TestClient, populated: str, factory: sessionmaker[Session]
) -> None:
    first = owner_client.get(events_url(populated)).json()
    second = owner_client.get(events_url(populated), params={"cursor": first["next_cursor"]})
    third = owner_client.get(events_url(populated), params={"cursor": second.json()["next_cursor"]})

    assert len(first["items"]) == PAGE_SIZE and first["next_cursor"]
    assert len(second.json()["items"]) == PAGE_SIZE and second.json()["next_cursor"]
    assert len(third.json()["items"]) == 20 and third.json()["next_cursor"] is None
    items = first["items"] + second.json()["items"] + third.json()["items"]
    ids = [item["id"] for item in items]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert ids == [event.id for event in stored(factory, populated)]


def test_concatenated_pages_pass_verify_chain(owner_client: TestClient, populated: str) -> None:
    items = fetch_all(owner_client, populated)

    assert len(items) == 1 + EXTRA_EVENTS
    assert all(set(item) == EVENT_FIELDS for item in items)
    assert items[1]["occurred_at"] == "2026-10-25T12:00:00.000000Z"
    result = verify_chain(items)
    assert result.valid, result
    assert result.checked == len(items)


def test_tampered_page_item_fails_verification(owner_client: TestClient, populated: str) -> None:
    items = fetch_all(owner_client, populated)
    items[70]["after"]["index"] = 999

    result = verify_chain(items)
    assert not result.valid
    assert result.first_invalid_id == items[70]["id"]
    assert result.reason is InvalidReason.HASH_MISMATCH


def test_only_the_case_chain_is_listed(
    owner_client: TestClient, populated: str, factory: sessionmaker[Session], owner: User
) -> None:
    items = fetch_all(owner_client, populated)

    assert {item["case_file_id"] for item in items} == {populated}
    with factory() as db:
        auth_events = db.scalars(select(Event).where(Event.case_file_id.is_(None))).all()
    assert any(event.actor_id == owner.id for event in auth_events)
    assert not {event.id for event in auth_events} & {item["id"] for item in items}


def test_non_member_gets_403_and_an_access_denied_event(
    other_client: TestClient, other: User, populated: str, factory: sessionmaker[Session]
) -> None:
    before = len(stored(factory, populated))

    response = other_client.get(events_url(populated))

    assert_problem(response, 403)
    assert "items" not in response.json()
    events = stored(factory, populated)
    assert len(events) == before + 1
    denied = events[-1]
    assert denied.kind == EventKind.ACCESS_DENIED.value
    assert denied.actor_id == other.id
    assert denied.after == {
        "method": "GET",
        "route": f"{API_V1_PREFIX}/case-files/{{case_id}}/events",
        "permission": "read",
        "status": 403,
    }


def test_read_member_can_list_events(
    owner_client: TestClient, other_client: TestClient, other: User, case_id: str
) -> None:
    added = owner_client.post(
        f"{CASES_URL}/{case_id}/members", json={"user_id": str(other.id), "permissions": ["read"]}
    )
    assert added.status_code == 201, added.text

    items = fetch_all(other_client, case_id)

    assert [item["kind"] for item in items] == ["case_file.created", "member.added"]
    assert verify_chain(items).valid


def test_kind_filter_returns_only_that_kind(owner_client: TestClient, populated: str) -> None:
    items = fetch_all(owner_client, populated, kind="source.ingested")

    assert len(items) == 40
    assert {item["kind"] for item in items} == {"source.ingested"}
    ids = [item["id"] for item in items]
    assert ids == sorted(ids)


def test_plan_id_filter_returns_only_that_plan(owner_client: TestClient, populated: str) -> None:
    items = fetch_all(owner_client, populated, plan_id=str(PLAN_ID))
    combined = fetch_all(owner_client, populated, plan_id=str(PLAN_ID), kind="plan.created")
    none = fetch_all(owner_client, populated, plan_id=str(PLAN_ID), kind="source.ingested")

    assert items and {item["plan_id"] for item in items} == {str(PLAN_ID)}
    assert len(items) + len(fetch_all(owner_client, populated, plan_id=str(OTHER_PLAN_ID))) == 39
    assert combined == items
    assert none == []


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "not.a.kind"},
        {"plan_id": "not-a-uuid"},
        {"cursor": "!!!"},
        {"cursor": encode_cursor(1) + "x"},
        {"cursor": base64.urlsafe_b64encode(b"-5").decode()},
        {"cursor": base64.urlsafe_b64encode(b"0012").decode()},
        {"cursor": encode_cursor(2**63)},
        {"cursor": "A" * 65},
    ],
)
def test_invalid_filters_and_cursors_are_422(
    owner_client: TestClient, case_id: str, params: dict[str, str]
) -> None:
    assert_problem(owner_client.get(events_url(case_id), params=params), 422)


def test_cursor_round_trip() -> None:
    assert decode_cursor(encode_cursor(0)) == 0
    assert decode_cursor(encode_cursor(123456789)) == 123456789
    assert "=" not in encode_cursor(1)


def test_missing_case_is_404_and_anonymous_is_401(
    app: FastAPI, owner_client: TestClient, stack: ExitStack, factory: sessionmaker[Session]
) -> None:
    assert_problem(owner_client.get(events_url(str(uuid.uuid4()))), 404)
    assert_problem(owner_client.get(events_url("not-a-uuid")), 404)
    anonymous = stack.enter_context(TestClient(app, base_url=BASE_URL))
    assert_problem(anonymous.get(events_url(str(uuid.uuid4()))), 401)
    with factory() as db:
        denied = db.scalars(select(Event).where(Event.kind == EventKind.ACCESS_DENIED.value))
        assert denied.all() == []
