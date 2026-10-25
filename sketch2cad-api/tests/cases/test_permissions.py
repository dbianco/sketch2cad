"""Case permissions, the member endpoint and the ``access.denied`` / membership events."""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import (
    CSRF_COOKIE,
    get_audit_session_factory,
    require_case_permission,
    require_csrf,
)
from sketch2cad.api.errors import PROBLEM_CONTENT_TYPE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind, event_content
from sketch2cad.audit.chain import lock_case_file
from sketch2cad.audit.verify import verify_chain
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.cases.permissions import normalize_permissions
from sketch2cad.db.base import Base
from sketch2cad.db.models import CaseFile, CaseMember, Event, User, UserRole
from sketch2cad.db.session import get_session

LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
CASES_URL = f"{API_V1_PREFIX}/case-files"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
BASE_URL = "https://testserver"
EDIT_PROBE = "/case-files/{case_id}/probe-edit"
DOWNLOAD_PROBE = "/case-files/{case_id}/probe-download"
DELIVER_PROBE = "/case-files/{case_id}/probe-deliver"
DIRTY_PROBE = "/case-files/{case_id}/probe-dirty"
DIRTY_USERNAME = "uncommitted-by-denied-request"


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


def dirty_request_session(case_id: str, db: Annotated[Session, Depends(get_session)]) -> None:
    """Leave an uncommitted insert and the case file row lock in the request session."""
    lock_case_file(db, uuid.UUID(case_id))
    db.add(User(username=DIRTY_USERNAME, password_hash="x", role=UserRole.TECHNICIAN))  # noqa: S106
    db.flush()


def make_probe(permission: str) -> Any:
    def probe(
        case_file: Annotated[CaseFile, Depends(require_case_permission(permission))],
    ) -> dict[str, str]:
        return {"id": str(case_file.id)}

    return probe


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]) -> FastAPI:
    """The real app plus probe routes standing in for the edit and download routes to come."""
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    application = create_app()
    probes: tuple[tuple[str, str, str, list[Any]], ...] = (
        ("PATCH", EDIT_PROBE, "edit", []),
        ("GET", DOWNLOAD_PROBE, "read", []),
        ("POST", DELIVER_PROBE, "deliver", []),
        ("PATCH", DIRTY_PROBE, "edit", [Depends(dirty_request_session)]),
    )
    for method, path, permission, extra in probes:
        application.add_api_route(
            f"{API_V1_PREFIX}{path}",
            make_probe(permission),
            methods=[method],
            dependencies=[Depends(require_csrf), *extra],
        )
    return application


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


def insert_user(factory: sessionmaker[Session], role: UserRole = UserRole.TECHNICIAN) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=role,
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


def url(path: str, case_id: str) -> str:
    return f"{API_V1_PREFIX}{path.format(case_id=case_id)}"


def call(client: TestClient, method: str, path: str, case_id: str) -> Any:
    return client.request(method, url(path, case_id))


def add_member(client: TestClient, case_id: str, user_id: uuid.UUID | str, perms: list[str]) -> Any:
    return client.post(
        f"{CASES_URL}/{case_id}/members", json={"user_id": str(user_id), "permissions": perms}
    )


def events(
    factory: sessionmaker[Session], case_id: str, kind: EventKind | None = None
) -> list[Event]:
    with factory() as db:
        statement = select(Event).where(Event.case_file_id == uuid.UUID(case_id))
        if kind is not None:
            statement = statement.where(Event.kind == kind.value)
        return list(db.scalars(statement.order_by(Event.id)).all())


def assert_problem(response: Any, status: int) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


READ_EDIT_DOWNLOAD = [
    ("GET", "/case-files/{case_id}", "read", "/api/v1/case-files/{case_id}"),
    ("PATCH", EDIT_PROBE, "edit", f"{API_V1_PREFIX}{EDIT_PROBE}"),
    ("GET", DOWNLOAD_PROBE, "read", f"{API_V1_PREFIX}{DOWNLOAD_PROBE}"),
]


def test_non_member_gets_404_and_one_access_denied_per_attempt(
    other_client: TestClient, other: User, case_id: str, factory: sessionmaker[Session]
) -> None:
    missing = other_client.get(f"{CASES_URL}/{uuid.uuid4()}")

    for attempt, (method, path, permission, route) in enumerate(READ_EDIT_DOWNLOAD, start=1):
        response = call(other_client, method, path, case_id)

        assert_problem(response, 404)
        assert response.json() == missing.json()
        denied = events(factory, case_id, EventKind.ACCESS_DENIED)
        assert len(denied) == attempt
        event = denied[-1]
        assert event.actor_id == other.id
        assert event.actor_role == "technician"
        assert event.client_ip
        assert event.after == {
            "method": method,
            "route": route,
            "permission": permission,
            "status": 404,
        }


def test_read_member_can_read_but_not_edit_or_deliver(
    owner_client: TestClient,
    other_client: TestClient,
    other: User,
    case_id: str,
    factory: sessionmaker[Session],
) -> None:
    created = add_member(owner_client, case_id, other.id, ["read"])
    assert created.status_code == 201, created.text
    assert created.json() == {
        "case_file_id": case_id,
        "user_id": str(other.id),
        "permissions": ["read"],
    }

    assert call(other_client, "GET", "/case-files/{case_id}", case_id).status_code == 200
    assert call(other_client, "GET", DOWNLOAD_PROBE, case_id).status_code == 200
    assert events(factory, case_id, EventKind.ACCESS_DENIED) == []

    assert_problem(call(other_client, "PATCH", EDIT_PROBE, case_id), 403)
    assert_problem(call(other_client, "POST", DELIVER_PROBE, case_id), 403)
    denied = events(factory, case_id, EventKind.ACCESS_DENIED)
    assert [(e.after or {})["permission"] for e in denied] == ["edit", "deliver"]
    assert all((e.after or {})["status"] == 403 for e in denied)
    assert len(other_client.get(CASES_URL).json()["items"]) == 1


def test_creating_a_case_and_adding_a_member_write_chained_events(
    owner_client: TestClient, owner: User, other: User, case_id: str, factory: sessionmaker[Session]
) -> None:
    assert (
        add_member(owner_client, case_id, other.id, ["deliver", "read", "read"]).status_code == 201
    )

    chain = events(factory, case_id)
    assert [event.kind for event in chain] == ["case_file.created", "member.added"]
    created, added = chain
    assert created.actor_id == owner.id
    assert (created.after or {})["code"].startswith("EXP-")
    assert (created.after or {})["incident_date"] == "2026-10-20"
    assert added.actor_id == owner.id
    assert added.after == {"user_id": str(other.id), "permissions": ["read", "deliver"]}
    assert verify_chain([event_content(e) | {"hash": e.hash} for e in chain]).valid
    with factory() as db:
        member = db.get(CaseMember, (uuid.UUID(case_id), other.id))
    assert member is not None and member.permissions == ["read", "deliver"]


def test_only_admin_or_creator_may_add_members(
    app: FastAPI,
    stack: ExitStack,
    owner_client: TestClient,
    other: User,
    case_id: str,
    factory: sessionmaker[Session],
) -> None:
    editor = insert_user(factory)
    stranger = insert_user(factory)
    admin = insert_user(factory, UserRole.ADMIN)
    assert (
        add_member(owner_client, case_id, editor.id, ["read", "edit", "deliver"]).status_code == 201
    )

    by_member = add_member(signed_in(app, stack, editor), case_id, other.id, ["read"])
    by_stranger = add_member(signed_in(app, stack, stranger), case_id, other.id, ["read"])
    by_admin = add_member(signed_in(app, stack, admin), case_id, other.id, ["read"])

    assert_problem(by_member, 403)
    assert_problem(by_stranger, 404)
    assert by_admin.status_code == 201
    denied = events(factory, case_id, EventKind.ACCESS_DENIED)
    assert [(e.actor_id, (e.after or {})["status"]) for e in denied] == [
        (editor.id, 403),
        (stranger.id, 404),
    ]
    assert all((e.after or {})["permission"] == "manage_members" for e in denied)
    added = events(factory, case_id, EventKind.MEMBER_ADDED)
    assert [(e.actor_id, e.actor_role) for e in added][-1] == (admin.id, "admin")


def test_admin_role_grants_no_case_permission(
    app: FastAPI, stack: ExitStack, case_id: str, factory: sessionmaker[Session]
) -> None:
    admin_client = signed_in(app, stack, insert_user(factory, UserRole.ADMIN))

    assert_problem(admin_client.get(f"{CASES_URL}/{case_id}"), 404)
    assert len(events(factory, case_id, EventKind.ACCESS_DENIED)) == 1


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"permissions": ["read"], "user_id": None}, 422),
        ({"permissions": []}, 422),
        ({"permissions": ["admin"]}, 422),
        ({"permissions": ["read"], "user_id": "not-a-uuid"}, 422),
    ],
)
def test_invalid_member_bodies_return_422(
    owner_client: TestClient, other: User, case_id: str, body: dict[str, Any], status: int
) -> None:
    payload = {"user_id": str(other.id)} | body

    assert_problem(owner_client.post(f"{CASES_URL}/{case_id}/members", json=payload), status)


def test_unknown_user_is_422_and_existing_member_is_409(
    owner_client: TestClient, owner: User, other: User, case_id: str, factory: sessionmaker[Session]
) -> None:
    assert_problem(add_member(owner_client, case_id, uuid.uuid4(), ["read"]), 422)
    assert add_member(owner_client, case_id, other.id, ["read"]).status_code == 201
    assert_problem(add_member(owner_client, case_id, other.id, ["edit"]), 409)
    assert_problem(add_member(owner_client, case_id, owner.id, ["read"]), 409)

    assert len(events(factory, case_id, EventKind.MEMBER_ADDED)) == 1
    with factory() as db:
        member = db.get(CaseMember, (uuid.UUID(case_id), other.id))
    assert member is not None and member.permissions == ["read"]


def test_add_member_without_csrf_is_403_without_access_denied(
    owner_client: TestClient, other: User, case_id: str, factory: sessionmaker[Session]
) -> None:
    del owner_client.headers[CSRF_HEADER]

    assert_problem(add_member(owner_client, case_id, other.id, ["read"]), 403)
    assert events(factory, case_id, EventKind.ACCESS_DENIED) == []


def test_missing_or_malformed_case_is_404_without_events(
    owner_client: TestClient, factory: sessionmaker[Session]
) -> None:
    assert_problem(owner_client.get(f"{CASES_URL}/not-a-uuid"), 404)
    assert_problem(owner_client.get(f"{CASES_URL}/{uuid.uuid4()}"), 404)
    assert_problem(add_member(owner_client, str(uuid.uuid4()), uuid.uuid4(), ["read"]), 404)
    with factory() as db:
        assert db.scalars(select(Event)).all() == []


def test_access_denied_is_committed_apart_from_the_rolled_back_request(
    other_client: TestClient, other: User, case_id: str, factory: sessionmaker[Session]
) -> None:
    # The request session holds the case row lock and an uncommitted insert when denied.
    response = call(other_client, "PATCH", DIRTY_PROBE, case_id)

    assert_problem(response, 404)
    denied = events(factory, case_id, EventKind.ACCESS_DENIED)
    assert [e.actor_id for e in denied] == [other.id]
    with factory() as db:
        assert db.scalars(select(User).where(User.username == DIRTY_USERNAME)).all() == []


def test_denial_stands_when_the_event_cannot_be_written(
    app: FastAPI, other_client: TestClient, case_id: str, factory: sessionmaker[Session]
) -> None:
    def broken_factory() -> Session:
        raise RuntimeError("audit database unavailable")

    app.dependency_overrides[get_audit_session_factory] = lambda: broken_factory

    assert_problem(other_client.get(f"{CASES_URL}/{case_id}"), 404)
    assert events(factory, case_id, EventKind.ACCESS_DENIED) == []


def test_normalize_permissions_dedupes_in_canonical_order() -> None:
    assert normalize_permissions(["deliver", "read", "deliver"]) == ["read", "deliver"]
    with pytest.raises(ValueError, match="admin"):
        normalize_permissions(["admin"])
