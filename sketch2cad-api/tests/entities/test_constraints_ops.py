"""``PATCH /entities/{id}`` with ``triangulate`` and ``baseline_offset`` (US-08, US-09, V-06, V-07).

Hand-checked geometry: references A = (0, 0) and B = (5, 0) with d_a = 3 and d_b = 4 give
the 3-4-5 candidates (1.8, 2.4) (left of A→B) and (1.8, -2.4). The baseline A = (1, 1) →
B = (4, 5) has direction (0.6, 0.8) and left normal (-0.8, 0.6), so s = 2.5, d = 1 gives
(1.7, 3.6) on the left and (3.3, 2.4) on the right.
"""

import math
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
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db.base import Base
from sketch2cad.db.models import (
    Calibration,
    Entity,
    Event,
    Evidence,
    Plan,
    PlanStatus,
    Revision,
    Source,
    User,
    UserRole,
)

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
REVISIONS_URL = f"{API_V1_PREFIX}/revisions"
ENTITIES_URL = f"{API_V1_PREFIX}/entities"
V04_MESSAGE = "Ingresa una distancia numérica mayor que cero, por ejemplo 3,50."
V06_MESSAGE = "Estas dos distancias no se cruzan. Revisa las medidas o las referencias elegidas."
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


def set_plan(factory: sessionmaker[Session], plan_id: uuid.UUID, **values: Any) -> None:
    with factory() as db:
        plan = db.get(Plan, plan_id)
        assert plan is not None
        for name, value in values.items():
            setattr(plan, name, value)
        db.commit()


@pytest.fixture
def plan(client: TestClient, factory: sessionmaker[Session], owner: User) -> Plan:
    """A plan in ``EDITING`` with its draft revision 1, an accepted calibration and the lease."""
    body = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=body)
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
    response = client.post(f"{CASES_URL}/{case_id}/plans", json=body)
    assert response.status_code == 201, response.text
    plan_id = uuid.UUID(response.json()["id"])
    set_plan(factory, plan_id, status=PlanStatus.EDITING)
    with factory() as db:
        db.add(Calibration(plan_id=plan_id, method="scan_dpi", accepted_by=owner.id))
        db.commit()
    response = client.post(f"{API_V1_PREFIX}/plans/{plan_id}/lease")
    assert response.status_code == 200, response.text
    with factory() as db:
        row = db.get(Plan, plan_id)
        assert row is not None
        return row


def point(x: float, y: float) -> dict[str, Any]:
    return {"kind": "point", "coords_m": [[x, y]]}


def symbol(x: float, y: float, symbol_id: str = "evidence_marker") -> dict[str, Any]:
    attributes = {"evidence_number": 7} if symbol_id == "evidence_marker" else {}
    return {"kind": "symbol", "anchor_m": [x, y], "symbol_id": symbol_id, "attributes": attributes}


def create_one(
    client: TestClient, plan: Plan, geometry: dict[str, Any], layer: str = "L_CONTROL"
) -> dict[str, Any]:
    body = {"entities": [{"type": geometry["kind"], "layer": layer, "geometry": geometry}]}
    response = client.post(f"{REVISIONS_URL}/{plan.current_revision_id}/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()["entities"][0]


def set_entity(factory: sessionmaker[Session], entity_id: str, **values: Any) -> None:
    with factory() as db:
        row = db.get(Entity, uuid.UUID(entity_id))
        assert row is not None
        for name, value in values.items():
            setattr(row, name, value)
        db.commit()


def reference(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    geometry: dict[str, Any],
    origin: str = "declared_dimension",
) -> str:
    """Id of a point-like entity with the given ``metric_origin``."""
    entity = create_one(client, plan, geometry)
    set_entity(factory, entity["id"], metric_origin=origin)
    return entity["id"]


@pytest.fixture
def corners(client: TestClient, plan: Plan, factory: sessionmaker[Session]) -> tuple[str, str]:
    """Metric references A = (0, 0) and B = (5, 0)."""
    return (
        reference(client, plan, factory, point(0.0, 0.0)),
        reference(client, plan, factory, point(5.0, 0.0)),
    )


def triangulate(
    client: TestClient, entity_id: str, ref_a: str, ref_b: str, d_a: Any, d_b: Any, **extra: Any
) -> Any:
    body = {
        "operation": "triangulate",
        "ref_a": ref_a,
        "ref_b": ref_b,
        "d_a_m": d_a,
        "d_b_m": d_b,
        **extra,
    }
    return client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)


def baseline(
    client: TestClient, entity_id: str, base_a: str, base_b: str, s: Any, d: Any, side: str
) -> Any:
    body = {
        "operation": "baseline_offset",
        "base_a": base_a,
        "base_b": base_b,
        "s_m": s,
        "d_m": d,
        "side": side,
    }
    return client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)


def entity_row(factory: sessionmaker[Session], entity_id: str) -> Entity:
    with factory() as db:
        row = db.get(Entity, uuid.UUID(entity_id))
        assert row is not None
        return row


def updated_events(factory: sessionmaker[Session]) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind == EventKind.ENTITY_UPDATED.value)
        return list(db.scalars(query.order_by(Event.id)))


def assert_rejected(response: Any, code: str | None, message: str | None = None) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body.get("code") == code
    if message is not None:
        assert body["detail"] == message


def assert_unchanged(factory: sessionmaker[Session], entity: dict[str, Any]) -> None:
    row = entity_row(factory, entity["id"])
    assert row.geometry == entity["geometry"]
    assert row.constraint is None
    assert row.metric_origin == entity["metric_origin"]
    assert updated_events(factory) == []


def test_3_4_5_triangulation_returns_both_candidates_and_applies_the_nearest(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))

    response = triangulate(client, target["id"], *corners, 3.0, 4.0)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidates"] == [
        pytest.approx([1.8, 2.4], abs=TOLERANCE),
        pytest.approx([1.8, -2.4], abs=TOLERANCE),
    ]
    assert body["chosen_candidate"] == 0
    (x, y) = body["geometry"]["coords_m"][0]
    assert (x, y) == pytest.approx((1.8, 2.4), abs=TOLERANCE)
    assert abs(math.dist((x, y), (0.0, 0.0)) - 3.0) <= TOLERANCE
    assert abs(math.dist((x, y), (5.0, 0.0)) - 4.0) <= TOLERANCE
    assert body["metric_origin"] == "triangulation"
    expected = {
        "kind": "triangulation",
        "refs": list(corners),
        "params": {"d_a_m": 3.0, "d_b_m": 4.0, "solution": "nearest"},
    }
    assert body["constraint"] == expected
    row = entity_row(factory, target["id"])
    assert row.constraint == expected
    assert row.metric_origin == "triangulation"


def test_nearest_follows_the_current_position_and_alt_picks_the_other_candidate(
    client: TestClient, plan: Plan, corners: tuple[str, str]
) -> None:
    below = create_one(client, plan, point(2.0, -1.0))
    other = create_one(client, plan, point(2.0, -1.0))

    nearest = triangulate(client, below["id"], *corners, 3.0, 4.0)
    alt = triangulate(client, other["id"], *corners, "3,0", "4", solution="alt")

    assert nearest.status_code == 200, nearest.text
    assert nearest.json()["chosen_candidate"] == 1
    assert nearest.json()["geometry"]["coords_m"][0] == pytest.approx([1.8, -2.4], abs=TOLERANCE)
    assert alt.status_code == 200, alt.text
    assert alt.json()["chosen_candidate"] == 0
    assert alt.json()["geometry"]["coords_m"][0] == pytest.approx([1.8, 2.4], abs=TOLERANCE)
    assert alt.json()["constraint"]["params"] == {"d_a_m": 3.0, "d_b_m": 4.0, "solution": "alt"}


@pytest.mark.parametrize("solution", ["nearest", "alt"])
def test_tangent_circles_give_a_single_candidate(
    client: TestClient, plan: Plan, corners: tuple[str, str], solution: str
) -> None:
    target = create_one(client, plan, point(1.0, 1.0))

    response = triangulate(client, target["id"], *corners, 2.0, 3.0, solution=solution)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidates"] == [pytest.approx([2.0, 0.0], abs=TOLERANCE)]
    assert body["chosen_candidate"] == 0
    assert body["geometry"]["coords_m"][0] == pytest.approx([2.0, 0.0], abs=TOLERANCE)


@pytest.mark.parametrize(("d_a", "d_b"), [(1.0, 1.0), (10.0, 1.0), ("1,5", "2,5")])
def test_distances_that_do_not_cross_return_422_v06_and_change_nothing(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    corners: tuple[str, str],
    d_a: Any,
    d_b: Any,
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))

    response = triangulate(client, target["id"], *corners, d_a, d_b)

    assert_rejected(response, "V-06", V06_MESSAGE)
    assert "no se cruzan" in response.json()["detail"]
    assert_unchanged(factory, target)


def test_references_at_the_same_position_do_not_cross(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    a = reference(client, plan, factory, point(1.0, 1.0))
    b = reference(client, plan, factory, point(1.0, 1.0))
    target = create_one(client, plan, point(0.0, 0.0))

    assert_rejected(triangulate(client, target["id"], a, b, 2.0, 2.0), "V-06", V06_MESSAGE)
    assert_unchanged(factory, target)


@pytest.mark.parametrize("origin", ["trace_only", "none"])
def test_a_non_metric_reference_returns_422_v07(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    corners: tuple[str, str],
    origin: str,
) -> None:
    traced = reference(client, plan, factory, point(5.0, 0.0), origin=origin)
    target = create_one(client, plan, point(1.0, 2.0))

    response = triangulate(client, target["id"], corners[0], traced, 3.0, 4.0)

    assert_rejected(response, "V-07", V07_MESSAGE)
    assert_unchanged(factory, target)
    response = baseline(client, target["id"], traced, corners[1], 1.0, 1.0, "left")
    assert_rejected(response, "V-07", V07_MESSAGE)
    assert_unchanged(factory, target)


def test_a_reference_that_depends_on_the_target_is_a_cycle_422_v07(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    a, b = corners
    p = create_one(client, plan, point(1.0, 2.0))
    assert triangulate(client, p["id"], a, b, 3.0, 4.0).status_code == 200
    q = create_one(client, plan, point(3.0, 3.0))
    assert baseline(client, q["id"], p["id"], b, 1.0, 0.5, "left").status_code == 200
    before = entity_row(factory, a)

    direct = triangulate(client, a, p["id"], b, 2.0, 5.0)
    transitive = baseline(client, a, q["id"], b, 1.0, 1.0, "right")
    itself = triangulate(client, p["id"], p["id"], b, 2.0, 2.0)

    for response in (direct, transitive, itself):
        assert_rejected(response, "V-07", V07_MESSAGE)
    row = entity_row(factory, a)
    assert (row.geometry, row.constraint) == (before.geometry, None)
    assert len(updated_events(factory)) == 2


def test_re_fixing_a_constrained_point_replaces_its_constraint(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))
    assert triangulate(client, target["id"], *corners, 3.0, 4.0).status_code == 200

    response = baseline(client, target["id"], *corners, 2.0, 1.0, "right")

    assert response.status_code == 200, response.text
    assert response.json()["metric_origin"] == "baseline_offset"
    assert response.json()["constraint"]["kind"] == "baseline_offset"
    assert response.json()["candidates"] is None


@pytest.mark.parametrize(("side", "expected"), [("left", (1.7, 3.6)), ("right", (3.3, 2.4))])
def test_baseline_offset_places_the_point_on_the_requested_side(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    side: str,
    expected: tuple[float, float],
) -> None:
    a = reference(client, plan, factory, point(1.0, 1.0))
    b = reference(client, plan, factory, point(4.0, 5.0), origin="baseline_offset")
    target = create_one(client, plan, point(0.0, 0.0))

    response = baseline(client, target["id"], a, b, "2,5", 1, side)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["geometry"]["coords_m"][0] == pytest.approx(list(expected), abs=TOLERANCE)
    assert body["metric_origin"] == "baseline_offset"
    assert body["candidates"] is None and body["chosen_candidate"] is None
    expected_constraint = {
        "kind": "baseline_offset",
        "refs": [a, b],
        "params": {"s_m": 2.5, "d_m": 1.0, "side": side},
    }
    assert body["constraint"] == expected_constraint
    assert entity_row(factory, target["id"]).constraint == expected_constraint


def test_baseline_accepts_zero_offset_and_negative_distance_along(
    client: TestClient, plan: Plan, corners: tuple[str, str]
) -> None:
    target = create_one(client, plan, point(1.0, 1.0))

    response = baseline(client, target["id"], *corners, "-1,5", "0", "left")

    assert response.status_code == 200, response.text
    assert response.json()["geometry"]["coords_m"][0] == pytest.approx([-1.5, 0.0], abs=TOLERANCE)
    assert response.json()["constraint"]["params"] == {"s_m": -1.5, "d_m": 0.0, "side": "left"}


@pytest.mark.parametrize(
    ("s", "d", "detail"),
    [
        (1.0, -0.5, "d_m must be zero or greater."),
        ("uno", 1.0, "s_m must be a number such as 1,50."),
        (1.0, "1e3", "d_m must be a number such as 1,50."),
    ],
)
def test_invalid_baseline_values_return_422_and_change_nothing(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    corners: tuple[str, str],
    s: Any,
    d: Any,
    detail: str,
) -> None:
    target = create_one(client, plan, point(1.0, 1.0))

    assert_rejected(baseline(client, target["id"], *corners, s, d, "left"), None, detail)
    assert_unchanged(factory, target)


def test_coincident_base_points_return_422(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    a = reference(client, plan, factory, point(2.0, 2.0))
    b = reference(client, plan, factory, point(2.0, 2.0))
    target = create_one(client, plan, point(0.0, 0.0))

    response = baseline(client, target["id"], a, b, 1.0, 1.0, "left")

    assert_rejected(response, None)
    assert "same position" in response.json()["detail"]
    assert_unchanged(factory, target)


@pytest.mark.parametrize("value", [0, -3.0, "0", "3,5 m"])
def test_triangulation_distances_follow_v04(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    corners: tuple[str, str],
    value: Any,
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))

    assert_rejected(triangulate(client, target["id"], *corners, value, 4.0), "V-04", V04_MESSAGE)
    assert_unchanged(factory, target)


def test_the_event_records_the_operation_before_after_and_constraint(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))

    response = triangulate(client, target["id"], *corners, 3.0, 4.0, reason="Cinta en terreno")

    assert response.status_code == 200, response.text
    (event,) = updated_events(factory)
    assert event.entity_id == uuid.UUID(target["id"])
    assert event.reason == "Cinta en terreno"
    assert event.before is not None and event.after is not None
    assert event.before["geometry"] == point(1.0, 2.0)
    assert (event.before["constraint"], event.before["metric_origin"]) == (None, "none")
    assert event.after["operation"] == "triangulate"
    assert event.after["metric_origin"] == "triangulation"
    assert event.after["constraint"] == response.json()["constraint"]
    assert event.after["geometry"] == response.json()["geometry"]


def test_an_evidence_symbol_is_fixed_and_can_serve_as_a_reference(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    owner: User,
    corners: tuple[str, str],
) -> None:
    marker = create_one(client, plan, symbol(1.0, 2.0), layer="L_EVIDENCE")
    with factory() as db:
        evidence = Evidence(case_file_id=plan.case_file_id, number=7, label="Casquillo")
        evidence.created_by = owner.id
        db.add(evidence)
        db.commit()
        evidence_id = evidence.id
    set_entity(factory, marker["id"], evidence_id=evidence_id)

    response = triangulate(client, marker["id"], *corners, 3.0, 4.0)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["geometry"]["anchor_m"] == pytest.approx([1.8, 2.4], abs=TOLERANCE)
    assert body["geometry"]["attributes"] == {"evidence_number": 7}
    assert body["evidence_id"] == str(evidence_id)
    target = create_one(client, plan, point(0.0, 0.0))
    placed = baseline(client, target["id"], corners[0], marker["id"], 3.0, 0.0, "left")
    assert placed.status_code == 200, placed.text
    assert placed.json()["geometry"]["coords_m"][0] == pytest.approx([1.8, 2.4], abs=TOLERANCE)


def test_a_trace_only_symbol_reference_is_rejected_with_v07(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    traced = reference(client, plan, factory, symbol(5.0, 0.0, "tree"), origin="trace_only")
    target = create_one(client, plan, point(1.0, 2.0))

    response = triangulate(client, target["id"], corners[0], traced, 3.0, 4.0)

    assert_rejected(response, "V-07", V07_MESSAGE)
    assert_unchanged(factory, target)


def test_invalid_references_and_targets_return_422(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    a, b = corners
    target = create_one(client, plan, point(1.0, 2.0))
    line = create_one(client, plan, {"kind": "line", "coords_m": [[0, 0], [5, 0]]})
    set_entity(factory, line["id"], metric_origin="declared_dimension")
    deleted = reference(client, plan, factory, point(5.0, 0.0))
    assert client.delete(f"{ENTITIES_URL}/{deleted}").status_code == 200

    cases = [
        (triangulate(client, target["id"], a, line["id"], 3.0, 4.0), "must be a point"),
        (triangulate(client, target["id"], a, deleted, 3.0, 4.0), "not a live entity"),
        (triangulate(client, target["id"], a, str(uuid.uuid4()), 3.0, 4.0), "not a live entity"),
        (triangulate(client, line["id"], a, b, 3.0, 4.0), "points and symbols only"),
    ]

    for response, detail in cases:
        assert_rejected(response, None)
        assert detail in response.json()["detail"]
    assert_unchanged(factory, target)


@pytest.mark.parametrize(
    "body",
    [
        {"ref_a": "A", "ref_b": "A", "d_a_m": 1, "d_b_m": 1},
        {"ref_a": "A", "d_a_m": 1, "d_b_m": 1},
        {"ref_a": "A", "ref_b": "B", "d_a_m": 1, "d_b_m": 1, "geometry": point(0, 0)},
        {"ref_a": "A", "ref_b": "B", "d_a_m": 1, "d_b_m": 1, "side": "left"},
        {"ref_a": "A", "ref_b": "B", "d_a_m": 1, "d_b_m": 1, "solution": "far"},
    ],
)
def test_malformed_triangulate_bodies_are_rejected(
    client: TestClient, plan: Plan, corners: tuple[str, str], body: dict[str, Any]
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))
    ids = {"A": corners[0], "B": corners[1]}
    body = {k: ids.get(v, v) if isinstance(v, str) else v for k, v in body.items()}

    response = client.patch(
        f"{ENTITIES_URL}/{target['id']}", json={"operation": "triangulate", **body}
    )

    assert response.status_code == 422, response.text


def test_moving_a_constrained_point_by_hand_drops_the_constraint(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))
    assert triangulate(client, target["id"], *corners, 3.0, 4.0).status_code == 200

    response = client.patch(
        f"{ENTITIES_URL}/{target['id']}", json={"operation": "move_vertex", "geometry": point(2, 2)}
    )

    assert response.status_code == 200, response.text
    assert (response.json()["constraint"], response.json()["metric_origin"]) == (None, "none")
    row = entity_row(factory, target["id"])
    assert (row.constraint, row.metric_origin) == (None, "none")
    event = updated_events(factory)[-1]
    assert event.before is not None and event.after is not None
    assert event.before["constraint"] is not None and event.after["constraint"] is None


def test_an_edit_that_keeps_the_position_keeps_the_constraint(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    marker = create_one(client, plan, symbol(1.0, 2.0), layer="L_EVIDENCE")
    fixed = triangulate(client, marker["id"], *corners, 3.0, 4.0).json()
    rotated = {**fixed["geometry"], "rotation_deg": 45.0}

    response = client.patch(
        f"{ENTITIES_URL}/{marker['id']}", json={"operation": "relabel", "geometry": rotated}
    )
    moved = client.patch(
        f"{ENTITIES_URL}/{marker['id']}", json={"operation": "reclassify", "layer": "L_SYMBOLS"}
    )

    assert response.status_code == 200, response.text
    assert moved.status_code == 200, moved.text
    assert moved.json()["constraint"] == fixed["constraint"]
    assert moved.json()["metric_origin"] == "triangulation"
    assert entity_row(factory, marker["id"]).constraint == fixed["constraint"]


def test_a_non_member_cannot_triangulate(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    corners: tuple[str, str],
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))
    stranger = signed_in(app, stack, insert_user(factory))

    response = triangulate(stranger, target["id"], *corners, 3.0, 4.0)

    assert response.status_code == 403, response.text
    assert_unchanged(factory, target)


def test_a_frozen_revision_rejects_constraint_operations_with_409(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))
    with factory() as db:
        revision = db.get(Revision, plan.current_revision_id)
        assert revision is not None
        revision.state = "frozen"
        revision.snapshot = {}
        revision.snapshot_sha256 = "0" * 64
        revision.frozen_at = datetime.now(UTC)
        db.commit()

    tri = triangulate(client, target["id"], *corners, 3.0, 4.0)
    base = baseline(client, target["id"], *corners, 1.0, 1.0, "left")

    for response in (tri, base):
        assert response.status_code == 409, response.text
        assert "frozen" in response.json()["detail"]
    assert_unchanged(factory, target)


def test_a_validated_plan_rejects_triangulation_with_409_v10(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], corners: tuple[str, str]
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))
    set_plan(factory, plan.id, status=PlanStatus.VALIDATED)

    response = triangulate(client, target["id"], *corners, 3.0, 4.0)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "V-10"
    assert_unchanged(factory, target)


def test_a_reference_from_another_revision_is_rejected(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    owner: User,
    corners: tuple[str, str],
) -> None:
    target = create_one(client, plan, point(1.0, 2.0))
    with factory() as db:
        other = Revision(plan_id=plan.id, number=2, created_by=owner.id)
        db.add(other)
        db.flush()
        foreign = Entity(
            revision_id=other.id,
            type="point",
            layer="L_CONTROL",
            geometry=point(5.0, 0.0),
            source={"method": "manual", "job_id": None, "parent_entity_id": None},
            review_status="accepted",
            metric_origin="declared_dimension",
        )
        db.add(foreign)
        db.commit()
        foreign_id = str(foreign.id)

    response = triangulate(client, target["id"], corners[0], foreign_id, 3.0, 4.0)

    assert_rejected(response, None)
    assert "not a live entity of this revision" in response.json()["detail"]
    assert_unchanged(factory, target)
