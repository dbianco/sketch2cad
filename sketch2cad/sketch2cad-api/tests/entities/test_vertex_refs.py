"""Vertices of lines and polylines as metric references (spec 11.3, D-15 PoC assumption).

PoC assumption (2026-10-26, to revisit with real sketches): ``triangulate`` and
``baseline_offset`` may take a wall corner ``{"entity_id": W, "vertex": i}`` as a reference
when the wall's ``metric_origin`` is metric (declared lengths or a metric scaled drawing).

Hand-checked geometry: wall corners A = (0, 0) and B = (6, 0) with d_a = d_b = 5 give the
3-4-5 candidates (3, 4) (left of A→B) and (3, -4). With the wall stretched to B = (8, 0)
the left candidate is (4, 3). The baseline (0, 0) → (6, 0) with s = 2, d = 1.5 gives
(2, -1.5) on the right; the baseline (6, 0) → (6, 4) has direction (0, 1) and left normal
(-1, 0), so s = 1.5, d = 2 gives (4, 1.5) on the left.
"""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
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
    Plan,
    PlanStatus,
    Source,
    User,
    UserRole,
)
from sketch2cad.entities.constraints import (
    TriangulationConstraint,
    constraint_to_json,
    parse_constraint,
    stored_reference_ids,
)
from sketch2cad.entities.schemas import VertexRef

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
ENTITIES_URL = f"{API_V1_PREFIX}/entities"
V07_MESSAGE = "Esta referencia no es métrica o depende del punto que intentas fijar."
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


def create(client: TestClient, plan: Plan, geometry: dict[str, Any], layer: str) -> str:
    body = {"entities": [{"type": geometry["kind"], "layer": layer, "geometry": geometry}]}
    url = f"{API_V1_PREFIX}/revisions/{plan.current_revision_id}/entities"
    response = client.post(url, json=body)
    assert response.status_code == 201, response.text
    entity_id: str = response.json()["entities"][0]["id"]
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


def wall(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    coords: list[list[float]],
    origin: str | None = "declared_dimension",
) -> str:
    """A wall (line for 2 vertices, polyline otherwise) with the given ``metric_origin``."""
    kind = "line" if len(coords) == 2 else "polyline"
    entity_id = create(client, plan, {"kind": kind, "coords_m": coords}, "L_STRUCTURE")
    if origin is not None:
        set_entity(factory, entity_id, metric_origin=origin)
    return entity_id


def target(client: TestClient, plan: Plan, x: float = 2.0, y: float = 3.0) -> str:
    return create(client, plan, {"kind": "point", "coords_m": [[x, y]]}, "L_CONTROL")


def vertex(entity_id: str, index: int) -> dict[str, Any]:
    return {"entity_id": entity_id, "vertex": index}


def patch(client: TestClient, entity_id: str, body: dict[str, Any]) -> Any:
    return client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)


def triangulate(client: TestClient, entity_id: str, a: Any, b: Any, d_a: Any, d_b: Any) -> Any:
    body = {"operation": "triangulate", "ref_a": a, "ref_b": b, "d_a_m": d_a, "d_b_m": d_b}
    return patch(client, entity_id, body)


def baseline(client: TestClient, entity_id: str, a: Any, b: Any, s: Any, d: Any, side: str) -> Any:
    body = {
        "operation": "baseline_offset",
        "base_a": a,
        "base_b": b,
        "s_m": s,
        "d_m": d,
        "side": side,
    }
    return patch(client, entity_id, body)


def position(factory: sessionmaker[Session], entity_id: str) -> list[float]:
    coords: list[float] = row(factory, entity_id).geometry["coords_m"][0]
    return coords


def recomputed(factory: sessionmaker[Session]) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind == EventKind.ENTITY_RECOMPUTED.value)
        return list(db.scalars(query.order_by(Event.id)))


def assert_rejected(response: Any, code: str | None, detail: str | None = None) -> None:
    assert response.status_code == 422, response.text
    assert response.json().get("code") == code
    if detail is not None:
        assert detail in str(response.json()["detail"])


def test_triangulation_from_two_declared_wall_corners_gives_the_hand_point(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0]])
    p = target(client, plan)

    response = triangulate(client, p, vertex(w, 0), vertex(w, 1), 5.0, "5,0")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidates"] == [
        pytest.approx([3.0, 4.0], abs=TOLERANCE),
        pytest.approx([3.0, -4.0], abs=TOLERANCE),
    ]
    assert body["chosen_candidate"] == 0
    assert body["geometry"]["coords_m"][0] == pytest.approx([3.0, 4.0], abs=TOLERANCE)
    assert body["metric_origin"] == "triangulation"
    expected = {
        "kind": "triangulation",
        "refs": [vertex(w, 0), vertex(w, 1)],
        "params": {"d_a_m": 5.0, "d_b_m": 5.0, "solution": "nearest", "side": "left"},
    }
    assert body["constraint"] == expected
    assert row(factory, p).constraint == expected


def test_a_wall_declared_by_fix_length_serves_and_stretching_it_recomputes_the_point(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [5.9, 0.0]], origin=None)
    declare = {"operation": "fix_length", "anchor_vertex": 0, "value_m": 6}
    assert patch(client, w, declare).status_code == 200
    assert row(factory, w).metric_origin == "declared_dimension"
    p = target(client, plan)
    assert triangulate(client, p, vertex(w, 0), vertex(w, 1), 5, 5).status_code == 200

    response = patch(client, w, {**declare, "value_m": "8,0"})

    assert response.status_code == 200, response.text
    assert position(factory, p) == pytest.approx([4.0, 3.0], abs=TOLERANCE)
    assert row(factory, p).metric_origin == "triangulation"
    events = recomputed(factory)
    assert len(events) == 1
    assert events[0].entity_id == uuid.UUID(p)
    assert events[0].after is not None
    assert events[0].after["trigger_entity_id"] == w
    assert events[0].after["constraint_error"] is None


@pytest.mark.parametrize("origin", ["none", "trace_only"])
def test_a_wall_without_declared_lengths_is_rejected_with_v07(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], origin: str
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0]], origin=origin)
    p = target(client, plan)

    response = triangulate(client, p, vertex(w, 0), vertex(w, 1), 5, 5)

    assert_rejected(response, "V-07")
    assert response.json()["detail"] == V07_MESSAGE
    assert row(factory, p).constraint is None
    assert position(factory, p) == [2.0, 3.0]


def test_invalid_vertex_references_return_422(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0]])
    p = target(client, plan)
    a = target(client, plan, 0.0, 0.0)
    set_entity(factory, a, metric_origin="declared_dimension")
    cases = [
        (triangulate(client, p, vertex(w, 0), vertex(w, 2), 5, 5), "has no vertex 2"),
        (triangulate(client, p, vertex(w, 0), vertex(a, 0), 5, 5), "a line or a polyline"),
        (triangulate(client, p, vertex(w, 0), w, 5, 5), "a point or a symbol"),
        (
            triangulate(client, p, vertex(w, 0), vertex(str(uuid.uuid4()), 0), 5, 5),
            "not a live entity",
        ),
    ]

    for response, detail in cases:
        assert_rejected(response, None, detail)
    assert row(factory, p).constraint is None


@pytest.mark.parametrize(
    "ref_b",
    [
        {"entity_id": "W", "vertex": -1},
        {"entity_id": "W", "vertex": "1"},
        {"entity_id": "W", "vertex": 1.0},
        {"entity_id": "W"},
        {"entity_id": "W", "vertex": 1, "extra": True},
        {"entity_id": "not-a-uuid", "vertex": 1},
        {"entity_id": "W", "vertex": 0},
    ],
    ids=["negative", "string", "float", "missing", "extra", "bad_id", "same_as_ref_a"],
)
def test_malformed_vertex_references_are_rejected(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], ref_b: dict[str, Any]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0]])
    p = target(client, plan)
    ref_b = {key: w if value == "W" else value for key, value in ref_b.items()}

    response = triangulate(client, p, vertex(w, 0), ref_b, 5, 5)

    assert response.status_code == 422, response.text


def test_a_polyline_corner_and_a_plain_point_can_be_mixed(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[-2.0, 3.0], [0.0, 0.0], [6.0, 4.0]], origin="scaled_drawing")
    b = target(client, plan, 6.0, 0.0)
    set_entity(factory, b, metric_origin="declared_dimension")
    p = target(client, plan, 3.0, -1.0)

    response = triangulate(client, p, vertex(w, 1), b, 5, 5)

    assert response.status_code == 200, response.text
    assert response.json()["geometry"]["coords_m"][0] == pytest.approx([3.0, -4.0], abs=TOLERANCE)
    assert response.json()["constraint"]["refs"] == [vertex(w, 1), b]


def test_baseline_offset_from_the_two_vertices_of_one_wall(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0]])
    p = target(client, plan)

    response = baseline(client, p, vertex(w, 0), vertex(w, 1), 2, "1,5", "right")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["geometry"]["coords_m"][0] == pytest.approx([2.0, -1.5], abs=TOLERANCE)
    assert body["metric_origin"] == "baseline_offset"
    assert body["constraint"] == {
        "kind": "baseline_offset",
        "refs": [vertex(w, 0), vertex(w, 1)],
        "params": {"s_m": 2.0, "d_m": 1.5, "side": "right"},
    }


def test_baseline_offset_along_a_polyline_side(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0], [6.0, 4.0]])
    p = target(client, plan)

    response = baseline(client, p, vertex(w, 1), vertex(w, 2), 1.5, 2, "left")

    assert response.status_code == 200, response.text
    assert response.json()["geometry"]["coords_m"][0] == pytest.approx([4.0, 1.5], abs=TOLERANCE)


def test_a_vertex_reference_to_the_target_is_a_cycle_v07(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0]])
    p = target(client, plan)
    set_entity(factory, p, metric_origin="declared_dimension")

    response = triangulate(client, p, vertex(w, 0), vertex(p, 0), 5, 5)

    assert_rejected(response, "V-07")
    assert response.json()["detail"] == V07_MESSAGE


def test_a_wall_that_depends_on_the_target_through_a_vertex_ref_is_a_cycle(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0]])
    p = target(client, plan)
    q = target(client, plan, 1.0, 1.0)
    assert triangulate(client, p, vertex(w, 0), vertex(w, 1), 5, 5).status_code == 200
    # A stored constraint on the wall that refers to Q would make Q -> W -> Q a cycle.
    stored = {
        "kind": "baseline_offset",
        "refs": [q, str(uuid.uuid4())],
        "params": {"s_m": 0.0, "d_m": 0.0, "side": "left"},
    }
    set_entity(factory, w, constraint=stored)

    response = triangulate(client, q, vertex(w, 0), p, 3, 4)

    assert_rejected(response, "V-07")


def test_a_vertex_that_no_longer_exists_marks_the_dependent_reference_missing(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0], [6.0, 4.0]])
    p = target(client, plan)
    assert baseline(client, p, vertex(w, 1), vertex(w, 2), 1.5, 2, "left").status_code == 200

    geometry = {"kind": "polyline", "coords_m": [[0.0, 0.0], [6.0, 0.0]]}
    response = patch(client, w, {"operation": "move_vertex", "geometry": geometry})

    assert response.status_code == 200, response.text
    entity = row(factory, p)
    assert entity.attributes["constraint_error"] == "reference_missing"
    assert entity.metric_origin == "none"
    assert entity.geometry["coords_m"][0] == pytest.approx([4.0, 1.5], abs=TOLERANCE)


def test_moving_a_wall_corner_by_hand_follows_it_but_leaves_the_point_non_metric(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    w = wall(client, plan, factory, [[0.0, 0.0], [6.0, 0.0]], origin=None)
    declare = {"operation": "fix_length", "anchor_vertex": 0, "value_m": 6}
    assert patch(client, w, declare).status_code == 200
    p = target(client, plan)
    assert triangulate(client, p, vertex(w, 0), vertex(w, 1), 5, 5).status_code == 200

    geometry = {"kind": "line", "coords_m": [[0.0, 0.0], [8.0, 0.0]]}
    response = patch(client, w, {"operation": "move_vertex", "geometry": geometry})

    assert response.status_code == 200, response.text
    assert row(factory, w).metric_origin == "none"
    entity = row(factory, p)
    assert entity.geometry["coords_m"][0] == pytest.approx([4.0, 3.0], abs=TOLERANCE)
    assert (entity.metric_origin, entity.attributes["constraint_error"]) == ("none", "V-07")


def test_stored_vertex_references_round_trip_and_plain_ids_keep_loading() -> None:
    wall_id, point_id = str(uuid.uuid4()), str(uuid.uuid4())
    mixed = {
        "kind": "triangulation",
        "refs": [{"entity_id": wall_id, "vertex": 3}, point_id],
        "params": {"d_a_m": 5.0, "d_b_m": 5.0, "solution": "nearest", "side": "left"},
    }
    same_wall = {
        "kind": "baseline_offset",
        "refs": [{"entity_id": wall_id, "vertex": 0}, {"entity_id": wall_id, "vertex": 1}],
        "params": {"s_m": 2.0, "d_m": 1.5, "side": "right"},
    }
    legacy = {**mixed, "refs": [wall_id, point_id]}

    constraint = parse_constraint(mixed)

    assert isinstance(constraint, TriangulationConstraint)
    assert constraint.refs == [
        VertexRef(entity_id=uuid.UUID(wall_id), vertex=3),
        uuid.UUID(point_id),
    ]
    for value in (mixed, same_wall, legacy):
        assert constraint_to_json(parse_constraint(value)) == value
    assert stored_reference_ids(mixed) == [uuid.UUID(wall_id), uuid.UUID(point_id)]
    assert stored_reference_ids(same_wall) == [uuid.UUID(wall_id), uuid.UUID(wall_id)]
    assert stored_reference_ids(legacy) == [uuid.UUID(wall_id), uuid.UUID(point_id)]
    assert stored_reference_ids({"refs": [{"vertex": 0}, "bad", point_id]}) == [uuid.UUID(point_id)]


@pytest.mark.parametrize(
    "ref",
    [
        {"entity_id": "W", "vertex": -1},
        {"entity_id": "W", "vertex": 0, "extra": 1},
        {"entity_id": "W"},
        {"entity_id": "W", "vertex": 0},
    ],
    ids=["negative_vertex", "extra_key", "missing_vertex", "same_ref_twice"],
)
def test_invalid_stored_vertex_references_are_rejected(ref: dict[str, Any]) -> None:
    wall_id = str(uuid.uuid4())
    ref = {key: wall_id if value == "W" else value for key, value in ref.items()}
    value = {
        "kind": "triangulation",
        "refs": [{"entity_id": wall_id, "vertex": 0}, ref],
        "params": {"d_a_m": 5.0, "d_b_m": 5.0, "solution": "nearest"},
    }

    with pytest.raises(ValidationError):
        parse_constraint(value)
