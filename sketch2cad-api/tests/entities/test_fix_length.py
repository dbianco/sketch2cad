"""``PATCH /entities/{id}`` with ``operation = fix_length`` (REQ-06, US-07, H3-6, V-04)."""

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
    Plan,
    PlanStatus,
    Revision,
    Source,
    User,
    UserRole,
)
from sketch2cad.entities.ops_fix_length import fix_length

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
REVISIONS_URL = f"{API_V1_PREFIX}/revisions"
ENTITIES_URL = f"{API_V1_PREFIX}/entities"
V04_MESSAGE = "Ingresa una distancia numérica mayor que cero, por ejemplo 3,50."
TOLERANCE = 1e-9
# Segment 0 is 3.00 m long along (0.6, 0.8); segments 1 and 2 are 2.00 m.
COORDS = [[1.0, 1.0], [2.8, 3.4], [4.8, 3.4], [4.8, 5.4]]
POLYLINE: dict[str, Any] = {"kind": "polyline", "coords_m": COORDS, "closed": False}
LINE: dict[str, Any] = {"kind": "line", "coords_m": [[0.0, 0.0], [0.0, 3.0]]}


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


def create_one(client: TestClient, plan: Plan, geometry: dict[str, Any]) -> dict[str, Any]:
    body = {"entities": [{"type": geometry["kind"], "layer": "L_STRUCTURE", "geometry": geometry}]}
    response = client.post(f"{REVISIONS_URL}/{plan.current_revision_id}/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()["entities"][0]


def patch(client: TestClient, entity_id: str, **body: Any) -> Any:
    return client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)


def fix(client: TestClient, entity_id: str, anchor: int, value: Any, **extra: Any) -> Any:
    response = patch(
        client, entity_id, operation="fix_length", anchor_vertex=anchor, value_m=value, **extra
    )
    return response


def updated_events(factory: sessionmaker[Session]) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind == EventKind.ENTITY_UPDATED.value)
        return list(db.scalars(query.order_by(Event.id)))


def entity_row(factory: sessionmaker[Session], entity_id: str) -> Entity:
    with factory() as db:
        row = db.get(Entity, uuid.UUID(entity_id))
        assert row is not None
        return row


def metric_status(factory: sessionmaker[Session], plan: Plan) -> str:
    with factory() as db:
        row = db.get(Plan, plan.id)
        assert row is not None
        return row.metric_status.value


def length(coords: list[list[float]], i: int, j: int) -> float:
    return math.dist(coords[i], coords[j])


def shift(before: list[float], after: list[float]) -> tuple[float, float]:
    return (after[0] - before[0], after[1] - before[1])


def test_fixing_4_20_on_a_3_00_segment_keeps_direction_and_shifts_later_vertices(
    client: TestClient, plan: Plan
) -> None:
    entity = create_one(client, plan, POLYLINE)
    old = COORDS
    assert length(old, 0, 1) == pytest.approx(3.0, abs=TOLERANCE)

    response = fix(client, entity["id"], 0, 4.2)

    assert response.status_code == 200, response.text
    new = response.json()["geometry"]["coords_m"]
    assert abs(length(new, 0, 1) - 4.2) <= TOLERANCE
    assert new[0] == old[0]
    assert new[1] == pytest.approx([1.0 + 4.2 * 0.6, 1.0 + 4.2 * 0.8], abs=TOLERANCE)
    vector = shift(old[1], new[1])
    assert vector == pytest.approx((1.2 * 0.6, 1.2 * 0.8), abs=TOLERANCE)
    for i in (2, 3):
        assert shift(old[i], new[i]) == pytest.approx(vector, abs=TOLERANCE)
    assert response.json()["attributes"]["declared_segments"] == {"0": 4.2}


def test_a_polyline_with_2_of_3_segments_declared_stays_trace_only(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, plan, POLYLINE)
    assert metric_status(factory, plan) == "approximate"

    assert fix(client, entity["id"], 0, 4.2).status_code == 200
    response = fix(client, entity["id"], 1, "2,50")

    assert response.status_code == 200, response.text
    assert response.json()["metric_origin"] == "trace_only"
    assert response.json()["attributes"]["declared_segments"] == {"0": 4.2, "1": 2.5}
    assert entity_row(factory, entity["id"]).metric_origin == "trace_only"
    assert metric_status(factory, plan) == "approximate"


def test_declaring_every_segment_makes_the_polyline_declared_dimension(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, plan, POLYLINE)

    for anchor, value in ((0, 4.2), (1, 2.5), (2, 1.75)):
        response = fix(client, entity["id"], anchor, value)
        assert response.status_code == 200, response.text

    body = response.json()
    assert body["metric_origin"] == "declared_dimension"
    coords = body["geometry"]["coords_m"]
    for k, value in enumerate((4.2, 2.5, 1.75)):
        assert abs(length(coords, k, k + 1) - value) <= TOLERANCE
    assert metric_status(factory, plan) == "metric"


def test_a_line_is_declared_dimension_with_its_one_segment(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, plan, LINE)

    response = fix(client, entity["id"], 1, 4.2)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metric_origin"] == "declared_dimension"
    assert body["geometry"]["coords_m"][1] == [0.0, 3.0]
    assert body["geometry"]["coords_m"][0] == pytest.approx([0.0, -1.2], abs=TOLERANCE)
    assert metric_status(factory, plan) == "metric"


def test_an_explicit_free_vertex_before_the_anchor_moves_the_vertices_before_it(
    client: TestClient, plan: Plan
) -> None:
    entity = create_one(client, plan, POLYLINE)
    old = COORDS

    response = fix(client, entity["id"], 2, 3.0, free_vertex=1)

    assert response.status_code == 200, response.text
    new = response.json()["geometry"]["coords_m"]
    assert abs(length(new, 1, 2) - 3.0) <= TOLERANCE
    assert new[2:] == old[2:]
    assert shift(old[0], new[0]) == pytest.approx((-1.0, 0.0), abs=TOLERANCE)
    assert shift(old[1], new[1]) == pytest.approx((-1.0, 0.0), abs=TOLERANCE)
    assert response.json()["attributes"]["declared_segments"] == {"1": 3.0}


@pytest.mark.parametrize("value", [0, 0.0, -1.5, "0", "-4,20", "abc"])
def test_a_zero_negative_or_non_numeric_value_is_422_v04_and_changes_nothing(
    client: TestClient, plan: Plan, factory: sessionmaker[Session], value: Any
) -> None:
    entity = create_one(client, plan, POLYLINE)

    response = fix(client, entity["id"], 0, value)

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == V04_MESSAGE
    assert response.json()["code"] == "V-04"
    row = entity_row(factory, entity["id"])
    assert (row.geometry, row.attributes, row.metric_origin) == (POLYLINE, {}, "none")
    assert updated_events(factory) == []


@pytest.mark.parametrize(
    ("geometry", "body"),
    [
        (POLYLINE, {"operation": "fix_length", "anchor_vertex": 0, "free_vertex": 2, "value_m": 1}),
        (POLYLINE, {"operation": "fix_length", "anchor_vertex": 4, "value_m": 1}),
        (POLYLINE, {"operation": "fix_length", "anchor_vertex": -1, "value_m": 1}),
        (POLYLINE, {"operation": "fix_length", "anchor_vertex": 0}),
        (POLYLINE, {"operation": "fix_length", "value_m": 1}),
        (POLYLINE, {"operation": "fix_length", "anchor_vertex": 0, "value_m": 1, "layer": "L_X"}),
        (POLYLINE, {"operation": "move_vertex", "geometry": POLYLINE, "value_m": 1}),
        (
            {"kind": "point", "coords_m": [[0.0, 0.0]]},
            {"operation": "fix_length", "anchor_vertex": 0, "value_m": 1},
        ),  # noqa: E501
        (
            {"kind": "line", "coords_m": [[1.0, 1.0], [1.0, 1.0]]},
            {"operation": "fix_length", "anchor_vertex": 0, "value_m": 1},
        ),  # noqa: E501
    ],
)
def test_invalid_fix_length_requests_are_422_and_change_nothing(
    client: TestClient,
    plan: Plan,
    factory: sessionmaker[Session],
    geometry: dict[str, Any],
    body: dict[str, Any],
) -> None:
    entity = create_one(client, plan, geometry)

    response = patch(client, entity["id"], **body)

    assert response.status_code == 422, response.text
    assert entity_row(factory, entity["id"]).geometry == geometry
    assert updated_events(factory) == []


def test_the_event_records_operation_fix_length_with_before_and_after(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, plan, POLYLINE)

    response = fix(client, entity["id"], 0, 4.2, reason="Medida de campo")

    assert response.status_code == 200, response.text
    [event] = updated_events(factory)
    assert event.entity_id == uuid.UUID(entity["id"])
    assert event.before is not None and event.after is not None
    assert event.after["operation"] == "fix_length"
    assert event.before["geometry"] == POLYLINE
    assert event.after["geometry"] == response.json()["geometry"]
    assert (event.before["attributes"], event.after["attributes"]) == (
        {},
        {"declared_segments": {"0": 4.2}},
    )
    assert (event.before["metric_origin"], event.after["metric_origin"]) == ("none", "trace_only")
    assert event.reason == "Medida de campo"


def test_a_non_member_cannot_fix_length(
    app: FastAPI, stack: ExitStack, client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, plan, POLYLINE)
    stranger = signed_in(app, stack, insert_user(factory))

    response = fix(stranger, entity["id"], 0, 4.2)

    assert response.status_code == 403, response.text
    assert entity_row(factory, entity["id"]).geometry == POLYLINE


def test_a_frozen_revision_rejects_fix_length_with_409(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, plan, POLYLINE)
    with factory() as db:
        revision = db.get(Revision, plan.current_revision_id)
        assert revision is not None
        revision.state = "frozen"
        revision.snapshot = {}
        revision.snapshot_sha256 = "0" * 64
        revision.frozen_at = datetime.now(UTC)
        db.commit()

    response = fix(client, entity["id"], 0, 4.2)

    assert response.status_code == 409, response.text
    assert "frozen" in response.json()["detail"]
    assert updated_events(factory) == []


def test_a_validated_plan_rejects_fix_length_with_409_v10(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, plan, POLYLINE)
    set_plan(factory, plan.id, status=PlanStatus.VALIDATED)

    response = fix(client, entity["id"], 0, 4.2)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "V-10"
    assert entity_row(factory, entity["id"]).geometry == POLYLINE


def declared_line(client: TestClient, plan: Plan) -> dict[str, Any]:
    entity = create_one(client, plan, LINE)
    response = fix(client, entity["id"], 0, 4.2)
    assert response.status_code == 200, response.text
    return response.json()


def test_moving_a_vertex_drops_the_declarations_whose_length_changed(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, plan, POLYLINE)
    for anchor, value in ((0, 4.2), (1, 2.5), (2, 1.75)):
        response = fix(client, entity["id"], anchor, value)
    geometry = response.json()["geometry"]
    coords = [list(point) for point in geometry["coords_m"]]
    coords[3] = [coords[3][0] + 0.5, coords[3][1]]

    moved = patch(
        client, entity["id"], operation="move_vertex", geometry={**geometry, "coords_m": coords}
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["attributes"]["declared_segments"] == {"0": 4.2, "1": 2.5}
    assert moved.json()["metric_origin"] == "trace_only"
    assert metric_status(factory, plan) == "approximate"


def test_moving_the_only_declared_segment_restores_the_hand_drawn_origin(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = declared_line(client, plan)
    geometry = {**LINE, "coords_m": [entity["geometry"]["coords_m"][0], [2.0, 2.0]]}

    moved = patch(client, entity["id"], operation="move_vertex", geometry=geometry)

    assert moved.status_code == 200, moved.text
    assert "declared_segments" not in moved.json()["attributes"]
    assert moved.json()["metric_origin"] == "none"
    assert metric_status(factory, plan) == "approximate"


def test_translating_or_relabelling_keeps_the_declarations(client: TestClient, plan: Plan) -> None:
    entity = declared_line(client, plan)
    coords = [[x + 10.0, y - 5.0] for x, y in entity["geometry"]["coords_m"]]

    moved = patch(
        client, entity["id"], operation="snap_grid", geometry={**LINE, "coords_m": coords}
    )
    relabelled = patch(client, entity["id"], operation="relabel", attributes={"label": "Muro"})

    assert moved.status_code == 200, moved.text
    assert moved.json()["metric_origin"] == "declared_dimension"
    assert relabelled.status_code == 200, relabelled.text
    assert relabelled.json()["attributes"] == {"label": "Muro", "declared_segments": {"0": 4.2}}
    assert relabelled.json()["metric_origin"] == "declared_dimension"


def test_clients_cannot_write_declared_segments_directly(client: TestClient, plan: Plan) -> None:
    entity = create_one(client, plan, LINE)
    item = {"type": "line", "layer": "L_STRUCTURE", "geometry": LINE}

    created = client.post(
        f"{REVISIONS_URL}/{plan.current_revision_id}/entities",
        json={"entities": [{**item, "attributes": {"declared_segments": 4.2}}]},
    )
    relabelled = patch(
        client, entity["id"], operation="relabel", attributes={"declared_segments": 4.2}
    )

    assert (created.status_code, relabelled.status_code) == (422, 422)


def test_the_closing_segment_of_a_closed_polyline_can_be_fixed() -> None:
    square = {"kind": "polyline", "coords_m": [[0, 0], [2, 0], [2, 2], [0, 2]], "closed": True}

    fixed = fix_length(square, {}, 0, 3, 3.0)

    assert fixed.segment == 3
    assert fixed.geometry["coords_m"][0] == [0.0, 0.0]
    assert fixed.geometry["coords_m"][3] == pytest.approx([0.0, 3.0], abs=TOLERANCE)
    assert fixed.attributes == {"declared_segments": {"3": 3.0}}
