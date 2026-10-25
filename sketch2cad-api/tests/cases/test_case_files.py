"""Case file creation with per-year codes, membership-filtered listing and retrieval."""

import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
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
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.cases.service import (
    NewCaseFile,
    create_case,
    escape_like,
    get_case_clock,
)
from sketch2cad.db.base import Base
from sketch2cad.db.models import CaseCodeCounter, CaseMember, IncidentType, User, UserRole

LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
CASES_URL = f"{API_V1_PREFIX}/case-files"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
BASE_URL = "https://testserver"
START = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
CODE_PATTERN = re.compile(r"^EXP-\d{4}-\d{4}$")
THREAD_TIMEOUT_SECONDS = 10


class FakeClock:
    """A controllable clock injected through the ``get_case_clock`` dependency."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


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
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AUTH_SESSION_TTL_MINUTES", raising=False)
    return monkeypatch


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def app(env: pytest.MonkeyPatch, factory: sessionmaker[Session], clock: FakeClock) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_case_clock] = lambda: clock
    return application


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


def insert_user(factory: sessionmaker[Session]) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name="Ana Pérez",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def signed_in(app: FastAPI, stack: ExitStack, user: User) -> TestClient:
    """Return a client logged in as ``user`` that sends the CSRF header on every request."""
    client = stack.enter_context(TestClient(app, base_url=BASE_URL))
    response = client.post(LOGIN_URL, json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def owner(factory: sessionmaker[Session]) -> User:
    return insert_user(factory)


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    return signed_in(app, stack, owner)


def case_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    body.update(overrides)
    return body


def create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post(CASES_URL, json=case_body(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def listed_codes(client: TestClient, **params: Any) -> list[str]:
    response = client.get(CASES_URL, params=params)
    assert response.status_code == 200
    return [item["code"] for item in response.json()["items"]]


def assert_not_found(response: Any) -> None:
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["status"] == 404


def test_create_returns_201_with_the_first_code_of_the_year(
    client: TestClient, owner: User
) -> None:
    body = create(client, organization="  Policía Científica  ")

    assert body["code"] == "EXP-2026-0001"
    assert body["incident_type"] == "traffic_collision"
    assert body["incident_date"] == "2026-10-20"
    assert body["description"] == "Colisión entre dos vehículos"
    assert body["declared_location"] == "Av. Libertador 1200"
    assert body["organization"] == "Policía Científica"
    assert body["status"] == "open"
    assert body["created_by"] == str(owner.id)
    assert create(client)["code"] == "EXP-2026-0002"


def test_creator_becomes_member_with_all_permissions(
    client: TestClient, owner: User, factory: sessionmaker[Session]
) -> None:
    body = create(client)

    with factory() as db:
        members = db.scalars(
            select(CaseMember).where(CaseMember.case_file_id == uuid.UUID(body["id"]))
        ).all()
    assert [(member.user_id, member.permissions) for member in members] == [
        (owner.id, ["read", "edit", "deliver"])
    ]
    fetched = client.get(f"{CASES_URL}/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_two_concurrent_creations_get_distinct_codes(
    app: FastAPI, stack: ExitStack, factory: sessionmaker[Session]
) -> None:
    clients = [signed_in(app, stack, insert_user(factory)) for _ in range(2)]
    barrier = threading.Barrier(len(clients))
    codes: list[str] = []
    errors: list[BaseException] = []

    def create_after_barrier(client: TestClient) -> None:
        try:
            barrier.wait(timeout=THREAD_TIMEOUT_SECONDS)
            codes.append(create(client)["code"])
        except BaseException as exc:  # reported by the main thread
            errors.append(exc)

    threads = [threading.Thread(target=create_after_barrier, args=(c,)) for c in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=THREAD_TIMEOUT_SECONDS)

    assert errors == []
    assert len(codes) == 2
    assert len(set(codes)) == 2
    assert all(CODE_PATTERN.match(code) for code in codes)
    assert sorted(codes) == ["EXP-2026-0001", "EXP-2026-0002"]


def test_code_allocation_waits_for_the_uncommitted_creation(
    factory: sessionmaker[Session], owner: User
) -> None:
    data = NewCaseFile(IncidentType.INSPECTION, START.date(), "Inspección", "Depósito 3")
    second_codes: list[str] = []

    def second_creation() -> None:
        with factory() as db:
            second_codes.append(create_case(db, owner.id, data, START).code)
            db.commit()

    with factory() as first:
        first_code = create_case(first, owner.id, data, START).code
        # The second upsert blocks on the year's row lock held by the open first transaction.
        thread = threading.Thread(target=second_creation)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive()
        first.commit()
    thread.join(timeout=THREAD_TIMEOUT_SECONDS)

    assert not thread.is_alive()
    assert (first_code, second_codes) == ("EXP-2026-0001", ["EXP-2026-0002"])


def test_year_rollover_restarts_numbering(
    client: TestClient, clock: FakeClock, factory: sessionmaker[Session]
) -> None:
    clock.now = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert create(client)["code"] == "EXP-2026-0001"
    assert create(client)["code"] == "EXP-2026-0002"

    clock.now = datetime(2027, 1, 1, 0, 0, tzinfo=UTC)
    assert create(client)["code"] == "EXP-2027-0001"

    with factory() as db:
        counters = {row.year: row.last_number for row in db.scalars(select(CaseCodeCounter))}
    assert counters == {2026: 2, 2027: 1}


def test_code_year_uses_utc(client: TestClient, clock: FakeClock) -> None:
    # 2026-12-31 22:00 at UTC-3 is already 2027-01-01 01:00 UTC.
    clock.now = datetime.fromisoformat("2026-12-31T22:00:00-03:00")

    assert create(client)["code"] == "EXP-2027-0001"


def test_non_member_gets_404_and_does_not_see_the_case(
    app: FastAPI, stack: ExitStack, client: TestClient, factory: sessionmaker[Session]
) -> None:
    case_id = create(client)["id"]
    outsider = signed_in(app, stack, insert_user(factory))

    response = outsider.get(f"{CASES_URL}/{case_id}")
    missing = outsider.get(f"{CASES_URL}/{uuid.uuid4()}")

    assert_not_found(response)
    assert_not_found(missing)
    assert response.json() == missing.json()
    assert listed_codes(outsider) == []


def test_added_member_sees_the_case(
    app: FastAPI, stack: ExitStack, client: TestClient, factory: sessionmaker[Session]
) -> None:
    body = create(client)
    member = insert_user(factory)
    with factory() as db:
        db.add(
            CaseMember(case_file_id=uuid.UUID(body["id"]), user_id=member.id, permissions=["read"])
        )
        db.commit()
    member_client = signed_in(app, stack, member)

    assert member_client.get(f"{CASES_URL}/{body['id']}").status_code == 200
    assert listed_codes(member_client) == [body["code"]]


def test_malformed_id_returns_404(client: TestClient) -> None:
    assert_not_found(client.get(f"{CASES_URL}/not-a-uuid"))


def test_other_incident_without_description_returns_422(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    body = case_body(incident_type="other")
    del body["description"]

    response = client.post(CASES_URL, json=body)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert ["body", "description"] in [error["loc"] for error in response.json()["errors"]]
    assert listed_codes(client) == []
    with factory() as db:
        assert db.scalars(select(CaseCodeCounter)).all() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"description": "   "},
        {"description": ""},
        {"declared_location": "  "},
        {"declared_location": None},
        {"incident_type": "fire"},
        {"incident_date": "not-a-date"},
    ],
)
def test_invalid_bodies_return_422(client: TestClient, overrides: dict[str, Any]) -> None:
    response = client.post(CASES_URL, json=case_body(**overrides))

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


def test_description_and_location_are_trimmed(client: TestClient) -> None:
    body = create(client, description="  Hurto  ", declared_location=" Calle 5 ", organization=" ")

    assert body["description"] == "Hurto"
    assert body["declared_location"] == "Calle 5"
    assert body["organization"] is None


def test_list_is_newest_first(client: TestClient) -> None:
    codes = [create(client)["code"] for _ in range(3)]

    assert listed_codes(client) == list(reversed(codes))


def test_list_filters_by_q_on_code_description_and_location(client: TestClient) -> None:
    by_description = create(client, description="Choque en ROTONDA norte")["code"]
    by_location = create(client, declared_location="Rotonda sur")["code"]
    create(client)

    assert set(listed_codes(client, q="rotonda")) == {by_description, by_location}
    assert listed_codes(client, q=by_location.lower()) == [by_location]
    assert len(listed_codes(client, q="   ")) == 3


def test_q_escapes_like_wildcards(client: TestClient) -> None:
    percent = create(client, description="Avance del 50% de obra")["code"]
    underscore = create(client, declared_location="Sector A_1")["code"]
    create(client, description="Avance del 500 de obra", declared_location="Sector AB1")

    assert listed_codes(client, q="50%") == [percent]
    assert listed_codes(client, q="A_1") == [underscore]
    assert listed_codes(client, q="%") == [percent]
    assert listed_codes(client, q="\\") == []
    assert escape_like("a\\b%c_d") == "a\\\\b\\%c\\_d"


def test_list_filters_by_incident_type(client: TestClient) -> None:
    inspection = create(client, incident_type="inspection")["code"]
    create(client, incident_type="crime_scene")

    assert listed_codes(client, incident_type="inspection") == [inspection]
    assert client.get(CASES_URL, params={"incident_type": "fire"}).status_code == 422


def test_list_pagination_and_limit_cap(client: TestClient) -> None:
    codes = list(reversed([create(client)["code"] for _ in range(3)]))

    assert listed_codes(client, limit=2) == codes[:2]
    assert listed_codes(client, limit=2, offset=2) == codes[2:]
    assert listed_codes(client, limit=10_000) == codes
    assert client.get(CASES_URL, params={"limit": 0}).status_code == 422
    assert client.get(CASES_URL, params={"offset": -1}).status_code == 422


def test_list_default_limit_is_50(
    client: TestClient, owner: User, factory: sessionmaker[Session]
) -> None:
    data = NewCaseFile(IncidentType.OTHER, START.date(), "Caso", "Lugar")
    with factory() as db:
        for _ in range(51):
            create_case(db, owner.id, data, START)
        db.commit()

    assert len(listed_codes(client)) == 50


def test_create_without_csrf_token_returns_403(client: TestClient) -> None:
    del client.headers[CSRF_HEADER]

    response = client.post(CASES_URL, json=case_body())

    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert listed_codes(client) == []


def test_unauthenticated_requests_return_401(app: FastAPI, stack: ExitStack) -> None:
    anonymous = stack.enter_context(TestClient(app, base_url=BASE_URL))

    for response in (
        anonymous.post(CASES_URL, json=case_body()),
        anonymous.get(CASES_URL),
        anonymous.get(f"{CASES_URL}/{uuid.uuid4()}"),
    ):
        assert response.status_code == 401
        assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
