"""Edit lease: acquire, renew, expire, release and the lease gate of edits (REQ-05, V-12)."""

import threading
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
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db.base import Base
from sketch2cad.db.models import (
    Calibration,
    CaseMember,
    Entity,
    Event,
    Plan,
    PlanStatus,
    Source,
    User,
    UserRole,
)
from sketch2cad.plans import lease

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
START = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
POLYLINE = {"kind": "polyline", "coords_m": [[0.0, 0.0], [4.2, 0.0]], "closed": False}
MOVED = {"kind": "polyline", "coords_m": [[0.0, 0.0], [5.0, 0.0]], "closed": False}
V12_ANA = "Ana Pérez está editando este plano. Entra en solo lectura o espera a que termine."


class FakeClock:
    """A controllable clock replacing ``lease.lease_clock``."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture(scope="module")
def tables(db_engine: Engine) -> Iterator[None]:
    """Create the application tables in the isolated schema and drop them afterwards."""
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
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock(START)
    monkeypatch.setattr(lease, "lease_clock", fake)
    return fake


@pytest.fixture
def app(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session], clock: FakeClock
) -> FastAPI:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    return create_app()


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


def insert_user(factory: sessionmaker[Session], display_name: str | None) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name=display_name,
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
    return insert_user(factory, "Ana Pérez")


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    return signed_in(app, stack, owner)


@pytest.fixture
def plan_id(client: TestClient, factory: sessionmaker[Session], owner: User) -> str:
    """A plan in ``EDITING`` with an active calibration and no lease."""
    body = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=body)
    assert response.status_code == 201, response.text
    case_id = uuid.UUID(response.json()["id"])
    with factory() as db:
        source = Source(
            case_file_id=case_id,
            original_filename="croquis.png",
            media_type="image/png",
            byte_size=1024,
            sha256=uuid.uuid4().hex * 2,
            storage_key=f"sources/{uuid.uuid4().hex}",
            uploaded_by=owner.id,
        )
        db.add(source)
        db.commit()
        source_id = str(source.id)
    response = client.post(
        f"{CASES_URL}/{case_id}/plans", json={"title": "Plano", "primary_source_id": source_id}
    )
    assert response.status_code == 201, response.text
    plan_id = response.json()["id"]
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        plan.status = PlanStatus.EDITING
        db.add(Calibration(plan_id=plan.id, method="scan_dpi", accepted_by=owner.id))
        db.commit()
    return plan_id


def member(
    app: FastAPI,
    stack: ExitStack,
    factory: sessionmaker[Session],
    plan_id: str,
    permissions: list[str],
    display_name: str | None = "Bruno Díaz",
) -> tuple[User, TestClient]:
    """A new user with ``permissions`` on the plan's case file, signed in."""
    user = insert_user(factory, display_name)
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        db.add(CaseMember(case_file_id=plan.case_file_id, user_id=user.id, permissions=permissions))
        db.commit()
    return user, signed_in(app, stack, user)


@pytest.fixture
def second(
    app: FastAPI, stack: ExitStack, factory: sessionmaker[Session], plan_id: str
) -> tuple[User, TestClient]:
    return member(app, stack, factory, plan_id, ["read", "edit"])


def take(client: TestClient, plan_id: str) -> Any:
    return client.post(f"{PLANS_URL}/{plan_id}/lease")


def lease_events(factory: sessionmaker[Session]) -> list[Event]:
    kinds = [EventKind.LEASE_ACQUIRED.value, EventKind.LEASE_RELEASED.value]
    with factory() as db:
        return list(db.scalars(select(Event).where(Event.kind.in_(kinds)).order_by(Event.id)))


def stored_lease(factory: sessionmaker[Session], plan_id: str) -> dict[str, Any] | None:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        return plan.edit_lease


def add_entity(client: TestClient, plan_id: str) -> str:
    revision_id = client.get(f"{PLANS_URL}/{plan_id}").json()["current_revision_id"]
    body = {"entities": [{"type": "polyline", "layer": "L_STRUCTURE", "geometry": POLYLINE}]}
    response = client.post(f"{API_V1_PREFIX}/revisions/{revision_id}/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()["entities"][0]["id"]


def test_acquire_returns_the_lease_shows_it_on_the_plan_and_writes_lease_acquired(
    client: TestClient, owner: User, plan_id: str, factory: sessionmaker[Session]
) -> None:
    response = take(client, plan_id)

    assert response.status_code == 200, response.text
    expected = {
        "user_id": str(owner.id),
        "display_name": "Ana Pérez",
        "expires_at": "2026-10-25T12:10:00Z",
    }
    assert response.json() == expected
    assert client.get(f"{PLANS_URL}/{plan_id}").json()["lease"] == expected
    assert stored_lease(factory, plan_id) == {
        "user_id": str(owner.id),
        "expires_at": "2026-10-25T12:10:00+00:00",
    }
    [event] = lease_events(factory)
    assert (event.kind, event.actor_id) == ("lease.acquired", owner.id)
    assert str(event.plan_id) == str(event.entity_id) == plan_id
    assert event.before is None
    assert event.after == stored_lease(factory, plan_id)


def test_renewal_by_the_holder_extends_the_lease_and_writes_no_event(
    client: TestClient, plan_id: str, clock: FakeClock, factory: sessionmaker[Session]
) -> None:
    assert take(client, plan_id).status_code == 200
    clock.advance(timedelta(minutes=7))

    response = take(client, plan_id)

    assert response.status_code == 200
    assert response.json()["expires_at"] == "2026-10-25T12:17:00Z"
    assert len(lease_events(factory)) == 1


def test_a_second_user_gets_409_with_the_holder_display_name(
    client: TestClient,
    owner: User,
    plan_id: str,
    second: tuple[User, TestClient],
    clock: FakeClock,
    factory: sessionmaker[Session],
) -> None:
    assert take(client, plan_id).status_code == 200
    before = stored_lease(factory, plan_id)
    clock.advance(timedelta(minutes=9, seconds=59))

    response = take(second[1], plan_id)

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["detail"] == V12_ANA
    assert body["code"] == "V-12"
    assert body["holder"] == {
        "user_id": str(owner.id),
        "display_name": "Ana Pérez",
        "expires_at": "2026-10-25T12:10:00Z",
    }
    assert stored_lease(factory, plan_id) == before
    assert len(lease_events(factory)) == 1


def test_the_holder_name_falls_back_to_the_username(
    app: FastAPI, stack: ExitStack, plan_id: str, factory: sessionmaker[Session]
) -> None:
    holder, holder_client = member(app, stack, factory, plan_id, ["edit"], display_name=None)
    _, other = member(app, stack, factory, plan_id, ["edit"])
    assert take(holder_client, plan_id).json()["display_name"] == holder.username

    response = take(other, plan_id)

    assert response.json()["detail"].startswith(f"{holder.username} está editando")


def test_after_11_minutes_without_renewal_the_second_user_obtains_the_lease(
    client: TestClient,
    owner: User,
    plan_id: str,
    second: tuple[User, TestClient],
    clock: FakeClock,
    factory: sessionmaker[Session],
) -> None:
    assert take(client, plan_id).status_code == 200
    first = stored_lease(factory, plan_id)
    clock.advance(timedelta(minutes=11))
    assert client.get(f"{PLANS_URL}/{plan_id}").json()["lease"] is None

    response = take(second[1], plan_id)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "user_id": str(second[0].id),
        "display_name": "Bruno Díaz",
        "expires_at": "2026-10-25T12:21:00Z",
    }
    acquired = lease_events(factory)[-1]
    assert (acquired.kind, acquired.actor_id) == ("lease.acquired", second[0].id)
    assert acquired.before == first
    assert take(client, plan_id).json()["holder"]["user_id"] == str(second[0].id)


def test_release_clears_the_lease_once_and_only_by_the_holder(
    client: TestClient,
    owner: User,
    plan_id: str,
    second: tuple[User, TestClient],
    factory: sessionmaker[Session],
) -> None:
    assert take(client, plan_id).status_code == 200

    denied = second[1].delete(f"{PLANS_URL}/{plan_id}/lease")
    released = client.delete(f"{PLANS_URL}/{plan_id}/lease")
    again = client.delete(f"{PLANS_URL}/{plan_id}/lease")

    assert (denied.status_code, denied.json()["code"]) == (409, "V-12")
    assert (released.status_code, released.json()) == (200, {"released": True})
    assert (again.status_code, again.json()) == (200, {"released": False})
    assert stored_lease(factory, plan_id) is None
    assert client.get(f"{PLANS_URL}/{plan_id}").json()["lease"] is None
    acquired, release = lease_events(factory)
    assert (release.kind, release.actor_id) == ("lease.released", owner.id)
    assert release.before == acquired.after
    assert release.after is None
    assert take(second[1], plan_id).status_code == 200


def test_an_entity_patch_by_a_user_without_the_lease_is_409(
    client: TestClient,
    plan_id: str,
    second: tuple[User, TestClient],
    clock: FakeClock,
    factory: sessionmaker[Session],
) -> None:
    assert take(client, plan_id).status_code == 200
    entity_id = add_entity(client, plan_id)
    body = {"operation": "move_vertex", "geometry": MOVED}

    held = second[1].patch(f"{API_V1_PREFIX}/entities/{entity_id}", json=body)
    clock.advance(timedelta(minutes=11))
    expired = client.patch(f"{API_V1_PREFIX}/entities/{entity_id}", json=body)

    assert held.status_code == 409
    assert (held.json()["code"], held.json()["detail"]) == ("V-12", V12_ANA)
    assert held.json()["holder"]["display_name"] == "Ana Pérez"
    assert (expired.status_code, expired.json()["code"]) == (409, "lease_required")
    with factory() as db:
        entity = db.get(Entity, uuid.UUID(entity_id))
        assert entity is not None
        assert entity.geometry == POLYLINE
        updates = db.scalars(select(Event).where(Event.kind == EventKind.ENTITY_UPDATED.value))
        assert list(updates) == []


def test_every_content_edit_requires_the_lease(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    assert take(client, plan_id).status_code == 200
    entity_id = add_entity(client, plan_id)
    revision_id = client.get(f"{PLANS_URL}/{plan_id}").json()["current_revision_id"]
    assert client.delete(f"{PLANS_URL}/{plan_id}/lease").json() == {"released": True}
    frame = {"origin_paper_mm": [10.0, 20.0], "x_axis_angle_deg": 0.0, "north_angle_deg": 0.0}
    item = {"type": "polyline", "layer": "L_STRUCTURE", "geometry": POLYLINE}

    responses = [
        client.post(f"{API_V1_PREFIX}/revisions/{revision_id}/entities", json={"entities": [item]}),
        client.patch(
            f"{API_V1_PREFIX}/entities/{entity_id}",
            json={"operation": "move_vertex", "geometry": MOVED},
        ),
        client.delete(f"{API_V1_PREFIX}/entities/{entity_id}"),
        client.put(f"{PLANS_URL}/{plan_id}/local-frame", json=frame),
    ]

    assert [(r.status_code, r.json()["code"]) for r in responses] == [(409, "lease_required")] * 4
    with factory() as db:
        assert len(list(db.scalars(select(Entity)))) == 1
        calibration = db.scalars(select(Calibration)).one()
        assert calibration.local_frame is None


def test_accepting_a_calibration_requires_the_lease(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        plan.status = PlanStatus.CALIBRATION_PENDING
        db.commit()
    url = f"{PLANS_URL}/{plan_id}/calibration/accept"

    rejected = client.post(url, json={"method": "none", "as_metric": False})
    assert take(client, plan_id).status_code == 200
    accepted = client.post(url, json={"method": "none", "as_metric": False})

    assert (rejected.status_code, rejected.json()["code"]) == (409, "lease_required")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "EDITING"


def test_concurrent_acquisitions_grant_the_lease_to_exactly_one_user(
    plan_id: str, second: tuple[User, TestClient], client: TestClient
) -> None:
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def acquire(target: TestClient) -> None:
        barrier.wait()
        statuses.append(take(target, plan_id).status_code)

    threads = [threading.Thread(target=acquire, args=(c,)) for c in (client, second[1])]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(statuses) == [200, 409]


def test_readers_strangers_and_missing_csrf_are_rejected(
    app: FastAPI, stack: ExitStack, client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    _, reader = member(app, stack, factory, plan_id, ["read"])
    stranger = signed_in(app, stack, insert_user(factory, None))
    del client.headers[CSRF_HEADER]

    statuses = [
        take(reader, plan_id).status_code,
        reader.delete(f"{PLANS_URL}/{plan_id}/lease").status_code,
        take(stranger, plan_id).status_code,
        take(client, plan_id).status_code,
    ]

    assert statuses == [403, 403, 403, 403]
    assert stored_lease(factory, plan_id) is None
