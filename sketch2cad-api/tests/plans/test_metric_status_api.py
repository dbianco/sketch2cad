"""GET /plans/{id}/metric-status and the recompute on every mutation path (REQ-07, US-11)."""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
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
    Entity,
    Event,
    MetricStatus,
    Plan,
    PlanStatus,
    Revision,
    Source,
    User,
    UserRole,
)
from sketch2cad.revisions.service import freeze, reopen

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
PLANS_URL = f"{API_V1_PREFIX}/plans"
POLYLINE = {"kind": "polyline", "coords_m": [[0.0, 0.0], [4.2, 0.0], [4.2, 3.1]], "closed": False}
DIMENSION = {"kind": "dimension", "p1_m": [0, 0], "p2_m": [4.24, 0], "declared_value_m": 4.2}


@pytest.fixture(scope="module")
def tables(db_engine: Engine) -> Iterator[None]:
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
    return insert_user(factory)


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    return signed_in(app, stack, owner)


@pytest.fixture
def plan(client: TestClient, factory: sessionmaker[Session], owner: User) -> Plan:
    """A plan in ``EDITING`` with draft revision 1, whose edit lease ``owner`` holds."""
    body = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(f"{API_V1_PREFIX}/case-files", json=body)
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]
    with factory() as db:
        source = Source(
            case_file_id=uuid.UUID(case_id),
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
    body = {"title": "Plano general", "primary_source_id": source_id}
    response = client.post(f"{API_V1_PREFIX}/case-files/{case_id}/plans", json=body)
    assert response.status_code == 201, response.text
    plan_id = uuid.UUID(response.json()["id"])
    with factory() as db:
        row = db.get(Plan, plan_id)
        assert row is not None
        row.status = PlanStatus.EDITING
        db.commit()
    assert client.post(f"{PLANS_URL}/{plan_id}/lease").status_code == 200
    with factory() as db:
        row = db.get(Plan, plan_id)
        assert row is not None
        return row


def calibrate(factory: sessionmaker[Session], plan: Plan, owner: User) -> None:
    with factory() as db:
        db.add(Calibration(plan_id=plan.id, method="scan_dpi", accepted_by=owner.id))
        db.commit()


def create(client: TestClient, plan: Plan, geometry: dict[str, Any], layer: str) -> str:
    item = {"type": geometry["kind"], "layer": layer, "geometry": geometry}
    url = f"{API_V1_PREFIX}/revisions/{plan.current_revision_id}/entities"
    response = client.post(url, json={"entities": [item]})
    assert response.status_code == 201, response.text
    return response.json()["entities"][0]["id"]


def set_origin(factory: sessionmaker[Session], entity_id: str, origin: str) -> None:
    with factory() as db:
        row = db.get(Entity, uuid.UUID(entity_id))
        assert row is not None
        row.metric_origin = origin
        db.commit()


def stored_status(factory: sessionmaker[Session], plan_id: uuid.UUID) -> str:
    with factory() as db:
        row = db.get(Plan, plan_id)
        assert row is not None
        return row.metric_status.value


def make_stale(factory: sessionmaker[Session], plan_id: uuid.UUID) -> None:
    """Store a wrong ``metric_status`` (raw SQL) to observe the next recompute."""
    with factory() as db:
        db.execute(text("UPDATE plan SET metric_status = 'metric' WHERE id = :id"), {"id": plan_id})
        db.commit()


def metric_status(client: TestClient, plan_id: uuid.UUID) -> dict[str, Any]:
    response = client.get(f"{PLANS_URL}/{plan_id}/metric-status")
    assert response.status_code == 200, response.text
    return response.json()


def test_without_calibration_the_plan_is_non_metric_with_no_blocking_entity(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    create(client, plan, POLYLINE, "L_STRUCTURE")

    assert metric_status(client, plan.id) == {
        "status": "non_metric",
        "blocking_entities": [],
        "reasons": {},
        "discrepant_dimensions": [],
        "length_conflicts": [],
    }
    assert stored_status(factory, plan.id) == "non_metric"


def test_the_endpoint_lists_exactly_the_blockers_and_matches_the_plan_field(
    client: TestClient, plan: Plan, owner: User, factory: sessionmaker[Session]
) -> None:
    calibrate(factory, plan, owner)
    declared = create(client, plan, POLYLINE, "L_STRUCTURE")
    set_origin(factory, declared, "declared_dimension")
    door = create(client, plan, POLYLINE, "L_ACCESS")
    traced = create(client, plan, POLYLINE, "L_STRUCTURE")
    set_origin(factory, traced, "trace_only")
    create(client, plan, POLYLINE, "L_ANNOTATIONS")
    dimension = create(client, plan, DIMENSION, "L_DIMENSIONS")

    body = metric_status(client, plan.id)

    assert body["status"] == "approximate" == stored_status(factory, plan.id)
    assert sorted(body["blocking_entities"]) == sorted([door, traced])
    assert body["reasons"] == {door: "none", traced: "trace_only"}
    assert body["discrepant_dimensions"] == [dimension]

    for blocker in (door, traced):
        assert client.delete(f"{API_V1_PREFIX}/entities/{blocker}").status_code == 200

    assert metric_status(client, plan.id) == {
        "status": "metric",
        "blocking_entities": [],
        "reasons": {},
        "discrepant_dimensions": [dimension],
        "length_conflicts": [],
    }
    assert stored_status(factory, plan.id) == "metric"


def test_the_metric_status_of_a_non_member_is_403_with_access_denied(
    app: FastAPI, stack: ExitStack, plan: Plan, factory: sessionmaker[Session]
) -> None:
    outsider = insert_user(factory)

    response = signed_in(app, stack, outsider).get(f"{PLANS_URL}/{plan.id}/metric-status")

    assert response.status_code == 403
    with factory() as db:
        denied = list(db.scalars(select(Event).where(Event.kind == EventKind.ACCESS_DENIED.value)))
    assert [event.actor_id for event in denied] == [outsider.id]


def test_setting_the_local_frame_recomputes_the_metric_status(
    client: TestClient, plan: Plan, owner: User, factory: sessionmaker[Session]
) -> None:
    calibrate(factory, plan, owner)
    create(client, plan, POLYLINE, "L_STRUCTURE")
    make_stale(factory, plan.id)

    frame = {"origin_paper_mm": [10.0, 20.0], "x_axis_angle_deg": 30.0, "north_angle_deg": 0.0}
    response = client.put(f"{PLANS_URL}/{plan.id}/local-frame", json=frame)

    assert response.status_code == 200, response.text
    assert response.json()["metric_status"] == "approximate" == stored_status(factory, plan.id)


def test_freeze_and_reopen_recompute_the_metric_status(
    client: TestClient, plan: Plan, owner: User, factory: sessionmaker[Session]
) -> None:
    calibrate(factory, plan, owner)
    create(client, plan, POLYLINE, "L_STRUCTURE")
    make_stale(factory, plan.id)

    with factory() as db:
        revision = db.get(Revision, plan.current_revision_id)
        user = db.get(User, owner.id)
        assert revision is not None and user is not None
        frozen = freeze(db, revision, user)
        db.commit()
        assert frozen.snapshot is not None
        assert frozen.snapshot["metric_status"] == "approximate"
    assert stored_status(factory, plan.id) == "approximate"

    make_stale(factory, plan.id)
    with factory() as db:
        row, user = db.get(Plan, plan.id), db.get(User, owner.id)
        assert row is not None and user is not None
        reopened = reopen(db, row, user)
        db.commit()
        assert row.current_revision_id == reopened.revision.id
        assert row.metric_status is MetricStatus.APPROXIMATE
    assert stored_status(factory, plan.id) == "approximate"
