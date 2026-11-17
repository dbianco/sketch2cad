"""Recompute of constrained entities when a reference moves (spec 11.3, REQ-06, H3-5).

Hand-checked geometry: references A = (0, 0) and B = (5, 0) with d_a = 3 and d_b = 4 give
the 3-4-5 candidates (1.8, 2.4) (left of A→B) and (1.8, -2.4). With A moved to (1, 0) the
left candidate is (2.125, sqrt(7.734375)) = (2.125, 2.781074...).
"""

import math
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
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
    Plan,
    PlanStatus,
    Source,
    User,
    UserRole,
)
from sketch2cad.entities.recompute import dependency_order, recompute_dependents
from sketch2cad.entities.schemas import EntityPatch
from sketch2cad.entities.service import entity_state, update_entity

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
ENTITIES_URL = f"{API_V1_PREFIX}/entities"
REMOVAL_MESSAGE = (
    "Este punto está fijado por una restricción métrica. Si lo mueves a mano se eliminará "
    "la restricción y dejará de ser métrico. Confirma para continuar."
)
MOVED_LEFT = (2.125, math.sqrt(7.734375))
TOLERANCE = 1e-9


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
def owner(factory: sessionmaker[Session]) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
        )
        db.add(user)
        db.commit()
        return user


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session], owner: User
) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    app: FastAPI = create_app()
    with ExitStack() as stack:
        test_client = stack.enter_context(TestClient(app, base_url="https://testserver"))
        body = {"username": owner.username, "password": PASSWORD}
        response = test_client.post(f"{API_V1_PREFIX}/auth/login", json=body)
        assert response.status_code == 200, response.text
        test_client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
        yield test_client


@pytest.fixture
def plan(client: TestClient, factory: sessionmaker[Session], owner: User) -> Plan:
    """A plan in ``EDITING`` with draft revision 1, an accepted calibration and the lease."""
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
        body = {"title": "Plano general", "primary_source_id": str(source.id)}
    response = client.post(f"{API_V1_PREFIX}/case-files/{case_id}/plans", json=body)
    assert response.status_code == 201, response.text
    plan_id = uuid.UUID(response.json()["id"])
    with factory() as db:
        row = db.get(Plan, plan_id)
        assert row is not None
        row.status = PlanStatus.EDITING
        db.add(Calibration(plan_id=plan_id, method="scan_dpi", accepted_by=owner.id))
        db.commit()
    assert client.post(f"{API_V1_PREFIX}/plans/{plan_id}/lease").status_code == 200
    with factory() as db:
        row = db.get(Plan, plan_id)
        assert row is not None
        return row


def point(x: float, y: float) -> dict[str, Any]:
    return {"kind": "point", "coords_m": [[x, y]]}


def create_point(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], x: float, y: float
) -> str:
    """Id of a new point at ``(x, y)`` made metric (``declared_dimension``)."""
    body = {"entities": [{"type": "point", "layer": "L_CONTROL", "geometry": point(x, y)}]}
    url = f"{API_V1_PREFIX}/revisions/{plan.current_revision_id}/entities"
    response = client.post(url, json=body)
    assert response.status_code == 201, response.text
    entity_id: str = response.json()["entities"][0]["id"]
    set_entity(factory, entity_id, metric_origin="declared_dimension")
    return entity_id


def set_entity(factory: sessionmaker[Session], entity_id: str, **values: Any) -> None:
    with factory() as db:
        row = db.get(Entity, uuid.UUID(entity_id))
        assert row is not None
        for name, value in values.items():
            setattr(row, name, value)
        db.commit()


def row(factory: sessionmaker[Session], entity_id: str) -> Entity:
    with factory() as db:
        entity = db.get(Entity, uuid.UUID(entity_id))
        assert entity is not None
        return entity


def position(factory: sessionmaker[Session], entity_id: str) -> tuple[float, float]:
    x, y = row(factory, entity_id).geometry["coords_m"][0]
    return (x, y)


def move(client: TestClient, entity_id: str, x: float, y: float, **extra: Any) -> Any:
    body = {"operation": "move_vertex", "geometry": point(x, y), **extra}
    return client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)


def fix(client: TestClient, entity_id: str, body: dict[str, Any]) -> None:
    response = client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)
    assert response.status_code == 200, response.text


def triangulate(client: TestClient, entity_id: str, a: str, b: str, **extra: Any) -> None:
    body = {"operation": "triangulate", "ref_a": a, "ref_b": b, "d_a_m": 3, "d_b_m": 4}
    fix(client, entity_id, {**body, **extra})


def recomputed(factory: sessionmaker[Session]) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind == EventKind.ENTITY_RECOMPUTED.value)
        return list(db.scalars(query.order_by(Event.id)))


@pytest.fixture
def fixed(client: TestClient, plan: Plan, factory: sessionmaker[Session]) -> tuple[str, str, str]:
    """References A = (0, 0), B = (5, 0) and P triangulated (3, 4) to (1.8, 2.4)."""
    a = create_point(client, plan, factory, 0.0, 0.0)
    b = create_point(client, plan, factory, 5.0, 0.0)
    p = create_point(client, plan, factory, 1.0, 2.0)
    triangulate(client, p, a, b)
    return a, b, p


def test_moving_a_reference_recomputes_the_dependent_with_one_event(
    client: TestClient, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    a, _, p = fixed

    response = move(client, a, 1.0, 0.0)

    assert response.status_code == 200, response.text
    assert position(factory, p) == pytest.approx(MOVED_LEFT, abs=TOLERANCE)
    entity = row(factory, p)
    assert (entity.metric_origin, entity.constraint is not None) == ("triangulation", True)
    events = recomputed(factory)
    assert len(events) == 1
    event = events[0]
    assert event.entity_id == uuid.UUID(p)
    assert event.before is not None and event.after is not None
    assert event.before["geometry"]["coords_m"][0] == pytest.approx([1.8, 2.4], abs=TOLERANCE)
    assert event.after["geometry"]["coords_m"][0] == pytest.approx(MOVED_LEFT, abs=TOLERANCE)
    assert event.after["trigger_entity_id"] == a
    assert event.after["constraint_error"] is None


def test_a_chain_is_recomputed_in_dependency_order_with_one_event_each(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    a, b, p = fixed
    # Q depends on A and on P (which depends on A); R depends on Q only (transitive).
    q = create_point(client, plan, factory, 0.0, 0.0)
    body = {"operation": "baseline_offset", "base_a": a, "base_b": p, "s_m": 1, "d_m": 0.5}
    fix(client, q, {**body, "side": "left"})
    r = create_point(client, plan, factory, 0.0, 0.0)
    fix(client, r, {"operation": "triangulate", "ref_a": q, "ref_b": b, "d_a_m": 2, "d_b_m": 4})

    assert move(client, a, 1.0, 0.0).status_code == 200

    pa, pp, pq, pr = (position(factory, e) for e in (a, p, q, r))
    ux, uy = (pp[0] - pa[0]) / 3.0, (pp[1] - pa[1]) / 3.0
    assert pq == pytest.approx((pa[0] + ux - 0.5 * uy, pa[1] + uy + 0.5 * ux), abs=TOLERANCE)
    assert math.dist(pr, pq) == pytest.approx(2.0, abs=TOLERANCE)
    assert math.dist(pr, (5.0, 0.0)) == pytest.approx(4.0, abs=TOLERANCE)
    assert [event.entity_id for event in recomputed(factory)] == [uuid.UUID(e) for e in (p, q, r)]


def test_dependency_order_puts_an_entity_after_the_ones_it_refers_to() -> None:
    graph = {"c": ["a", "b"], "b": ["a"], "x": ["y"], "y": ["x"]}

    assert dependency_order(graph, ["c", "b"]) == ["b", "c"]
    assert dependency_order(graph, ["x", "y"]) == ["x", "y"]


def test_a_cycle_stored_in_the_database_does_not_hang(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    a, b, p = fixed
    q = create_point(client, plan, factory, 1.8, -2.4)
    triangulate(client, q, a, b, solution="alt")
    # Corrupt the stored graph: P depends on Q and Q depends on P.
    constraint = row(factory, p).constraint
    assert constraint is not None
    set_entity(factory, p, constraint={**constraint, "refs": [a, q]})
    set_entity(factory, q, constraint={**constraint, "refs": [p, b]})

    assert move(client, a, 1.0, 0.0).status_code == 200

    ids = [event.entity_id for event in recomputed(factory)]
    assert len(ids) == 2 and set(ids) == {uuid.UUID(p), uuid.UUID(q)}


def test_recompute_is_idempotent_and_keeps_the_applied_side(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    a = create_point(client, plan, factory, 0.0, 0.0)
    b = create_point(client, plan, factory, 5.0, 0.0)
    p = create_point(client, plan, factory, 1.8, -2.0)
    triangulate(client, p, a, b, solution="alt")  # the other candidate: left, (1.8, 2.4)
    constraint = row(factory, p).constraint
    assert constraint is not None and constraint["params"]["side"] == "left"

    for x in (1.0, 0.5, 0.0):
        assert move(client, a, x, 0.0).status_code == 200
        assert position(factory, p)[1] > 0
    assert position(factory, p) == pytest.approx((1.8, 2.4), abs=TOLERANCE)
    with factory() as db:
        reference = db.get(Entity, uuid.UUID(a))
        assert reference is not None
        assert recompute_dependents(db, reference, entity_state) == []
        assert recompute_dependents(db, reference, entity_state) == []
    assert len(recomputed(factory)) == 3


def test_a_constraint_without_side_follows_the_nearest_candidate_stably(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    a, _, p = fixed
    constraint = row(factory, p).constraint
    assert constraint is not None
    params = {k: v for k, v in constraint["params"].items() if k != "side"}
    set_entity(factory, p, constraint={**constraint, "params": {**params, "solution": "alt"}})

    for _ in range(2):
        assert move(client, a, 1.0, 0.0).status_code == 200
        assert position(factory, p) == pytest.approx(MOVED_LEFT, abs=TOLERANCE)
        assert move(client, a, 0.0, 0.0).status_code == 200
        assert position(factory, p) == pytest.approx((1.8, 2.4), abs=TOLERANCE)


def test_an_impossible_recompute_keeps_the_position_and_marks_the_entity(
    client: TestClient, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    a, _, p = fixed

    assert move(client, a, -10.0, 0.0).status_code == 200

    entity = row(factory, p)
    assert position(factory, p) == pytest.approx((1.8, 2.4), abs=TOLERANCE)
    assert entity.constraint is not None
    assert (entity.metric_origin, entity.attributes) == ("none", {"constraint_error": "V-06"})
    (event,) = recomputed(factory)
    assert event.after is not None and event.after["constraint_error"] == "V-06"

    assert move(client, a, 0.0, 0.0).status_code == 200

    entity = row(factory, p)
    assert (entity.metric_origin, entity.attributes) == ("triangulation", {})
    assert len(recomputed(factory)) == 2


def test_deleting_a_reference_marks_its_dependents(
    client: TestClient, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    _, b, p = fixed

    assert client.delete(f"{ENTITIES_URL}/{b}").status_code == 200

    entity = row(factory, p)
    assert entity.attributes == {"constraint_error": "reference_missing"}
    assert entity.metric_origin == "none"
    assert [event.entity_id for event in recomputed(factory)] == [uuid.UUID(p)]


def test_a_non_metric_reference_is_followed_but_leaves_the_entity_non_metric(
    client: TestClient, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    a, _, p = fixed
    set_entity(factory, a, metric_origin="trace_only")

    assert move(client, a, 1.0, 0.0).status_code == 200

    entity = row(factory, p)
    assert position(factory, p) == pytest.approx(MOVED_LEFT, abs=TOLERANCE)
    assert (entity.metric_origin, entity.attributes) == ("none", {"constraint_error": "V-07"})


def test_constraint_error_cannot_be_set_by_a_request(
    client: TestClient, fixed: tuple[str, str, str]
) -> None:
    body = {"operation": "relabel", "attributes": {"constraint_error": "V-06"}}

    response = client.patch(f"{ENTITIES_URL}/{fixed[2]}", json=body)

    assert response.status_code == 422, response.text


def test_moving_a_constrained_point_needs_confirmation(
    client: TestClient, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    a, _, p = fixed

    rejected = move(client, p, 2.0, 2.0)

    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "constraint_removal_required"
    assert rejected.json()["detail"] == REMOVAL_MESSAGE
    entity = row(factory, p)
    assert (entity.metric_origin, entity.constraint is not None) == ("triangulation", True)
    assert position(factory, p) == pytest.approx((1.8, 2.4), abs=TOLERANCE)

    confirmed = move(client, p, 2.0, 2.0, confirm_constraint_removal=True)

    assert confirmed.status_code == 200, confirmed.text
    assert (confirmed.json()["constraint"], confirmed.json()["metric_origin"]) == (None, "none")
    assert move(client, a, 1.0, 0.0).status_code == 200
    assert position(factory, p) == (2.0, 2.0)
    assert recomputed(factory) == []


def test_a_frame_change_keeps_constraints_satisfied_without_recompute(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], fixed: tuple[str, str, str]
) -> None:
    a, b, p = fixed
    url = f"{API_V1_PREFIX}/plans/{plan.id}/local-frame"
    frame = {"origin_paper_mm": [80.25, 140.5], "x_axis_angle_deg": 33.7, "north_angle_deg": 15.0}

    response = client.put(url, json=frame)

    assert response.status_code == 200, response.text
    pa, pb, pp = (position(factory, e) for e in (a, b, p))
    assert pa != (0.0, 0.0)
    assert math.dist(pp, pa) == pytest.approx(3.0, abs=1e-6)
    assert math.dist(pp, pb) == pytest.approx(4.0, abs=1e-6)
    cross = (pb[0] - pa[0]) * (pp[1] - pa[1]) - (pb[1] - pa[1]) * (pp[0] - pa[0])
    assert cross > 0  # still to the left of A→B
    assert recomputed(factory) == []
    assert row(factory, p).metric_origin == "triangulation"


def test_recompute_events_are_written_in_the_transaction_of_the_move(
    plan: Plan, factory: sessionmaker[Session], owner: User, fixed: tuple[str, str, str]
) -> None:
    a, _, p = fixed
    patch = EntityPatch.model_validate({"operation": "move_vertex", "geometry": point(1, 0)})
    kind = EventKind.ENTITY_RECOMPUTED.value
    with factory() as db:
        user, entity = db.get(User, owner.id), db.get(Entity, uuid.UUID(a))
        assert user is not None and entity is not None
        update_entity(db, entity, user, patch)
        count = db.scalar(select(func.count()).select_from(Event).where(Event.kind == kind))
        assert count == 1
        db.rollback()

    assert recomputed(factory) == []
    assert position(factory, p) == pytest.approx((1.8, 2.4), abs=TOLERANCE)
