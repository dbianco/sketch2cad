"""Entity endpoints: create, read, patch and soft delete with provenance (REQ-05, US-06)."""

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
    CaseMember,
    Entity,
    Event,
    Plan,
    PlanStatus,
    Revision,
    Source,
    SourceMode,
    User,
    UserRole,
)

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
REVISIONS_URL = f"{API_V1_PREFIX}/revisions"
ENTITIES_URL = f"{API_V1_PREFIX}/entities"
POLYLINE = {"kind": "polyline", "coords_m": [[0.0, 0.0], [4.2, 0.0], [4.2, 3.1]], "closed": False}
SNAP_OPERATIONS = ["snap_orthogonal", "snap_endpoint", "snap_grid"]


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
def case_id(client: TestClient) -> str:
    body = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def set_plan(factory: sessionmaker[Session], plan_id: uuid.UUID, **values: Any) -> None:
    with factory() as db:
        plan = db.get(Plan, plan_id)
        assert plan is not None
        for name, value in values.items():
            setattr(plan, name, value)
        db.commit()


def make_plan(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> Plan:
    """A plan in ``EDITING`` with its draft revision 1."""
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
        plan = db.get(Plan, plan_id)
        assert plan is not None
        return plan


@pytest.fixture
def plan(client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User) -> Plan:
    return make_plan(client, factory, case_id, owner)


@pytest.fixture
def revision_id(plan: Plan) -> str:
    return str(plan.current_revision_id)


def entity_body(geometry: dict[str, Any], layer: str = "L_STRUCTURE", **extra: Any) -> Any:
    return {"type": geometry["kind"], "layer": layer, "geometry": geometry, **extra}


def post_entities(client: TestClient, revision_id: str, *items: dict[str, Any]) -> Any:
    return client.post(f"{REVISIONS_URL}/{revision_id}/entities", json={"entities": list(items)})


def create_one(client: TestClient, revision_id: str, item: dict[str, Any]) -> dict[str, Any]:
    response = post_entities(client, revision_id, item)
    assert response.status_code == 201, response.text
    return response.json()["entities"][0]


def list_ids(client: TestClient, revision_id: str) -> list[str]:
    response = client.get(f"{REVISIONS_URL}/{revision_id}/entities")
    assert response.status_code == 200, response.text
    return [entity["id"] for entity in response.json()["entities"]]


def patch(client: TestClient, entity_id: str, **body: Any) -> Any:
    return client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)


def events(factory: sessionmaker[Session], *kinds: EventKind) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind.in_([kind.value for kind in kinds]))
        return list(db.scalars(query.order_by(Event.id)))


def entity_rows(factory: sessionmaker[Session]) -> list[Entity]:
    with factory() as db:
        return list(db.scalars(select(Entity)))


def moved(geometry: dict[str, Any], index: int, to: list[float]) -> dict[str, Any]:
    coords = [list(point) for point in geometry["coords_m"]]
    coords[index] = to
    return {**geometry, "coords_m": coords}


def test_moving_vertex_1_records_before_and_after_differing_only_in_that_vertex(
    client: TestClient, revision_id: str, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))
    geometry = moved(POLYLINE, 1, [4.5, 0.25])

    response = patch(client, entity["id"], operation="move_vertex", geometry=geometry)

    assert response.status_code == 200, response.text
    assert response.json()["geometry"] == geometry
    [event] = events(factory, EventKind.ENTITY_UPDATED)
    assert (event.plan_id, event.revision_id, event.entity_id) == (
        plan.id,
        plan.current_revision_id,
        uuid.UUID(entity["id"]),
    )
    assert event.before is not None and event.after is not None
    after = dict(event.after)
    assert after.pop("operation") == "move_vertex"
    changed = {key for key in after if after[key] != event.before[key]}
    assert changed == {"geometry"}
    before_coords, after_coords = (
        event.before["geometry"]["coords_m"],
        after["geometry"]["coords_m"],
    )
    assert [i for i in range(3) if before_coords[i] != after_coords[i]] == [1]
    assert after_coords[1] == [4.5, 0.25]
    assert {k: v for k, v in after["geometry"].items() if k != "coords_m"} == {
        k: v for k, v in event.before["geometry"].items() if k != "coords_m"
    }
    assert event.reason is None


def test_editing_a_suggestion_keeps_its_source(
    client: TestClient, revision_id: str, factory: sessionmaker[Session]
) -> None:
    source = {"method": "cv", "job_id": str(uuid.uuid4()), "parent_entity_id": None}
    with factory() as db:
        entity = Entity(
            revision_id=uuid.UUID(revision_id),
            type="polyline",
            layer="L_STRUCTURE",
            geometry=POLYLINE,
            source=source,
            confidence=0.8,
            model_version="lines-1",
            review_status="suggested",
            metric_origin="trace_only",
        )
        db.add(entity)
        db.commit()
        entity_id = str(entity.id)

    response = patch(
        client,
        entity_id,
        operation="snap_orthogonal",
        geometry=moved(POLYLINE, 2, [4.2, 3.0]),
        reason="Muro recto",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == source
    assert (body["confidence"], body["model_version"]) == (0.8, "lines-1")
    [row] = entity_rows(factory)
    assert row.source == source
    [event] = events(factory, EventKind.ENTITY_UPDATED)
    assert event.before is not None and event.after is not None
    assert event.before["source"] == event.after["source"] == source
    assert event.reason == "Muro recto"


def test_delete_sets_deleted_at_hides_the_entity_and_keeps_the_row(
    client: TestClient, revision_id: str, plan: Plan, factory: sessionmaker[Session]
) -> None:
    kept = create_one(client, revision_id, entity_body(POLYLINE))
    gone = create_one(client, revision_id, entity_body({"kind": "point", "coords_m": [[1, 2]]}))

    response = client.delete(f"{ENTITIES_URL}/{gone['id']}", params={"reason": "Duplicado"})

    assert response.status_code == 200, response.text
    assert response.json()["deleted_at"] is not None
    assert list_ids(client, revision_id) == [kept["id"]]
    rows = {str(row.id): row for row in entity_rows(factory)}
    assert set(rows) == {kept["id"], gone["id"]}
    assert rows[gone["id"]].deleted_at is not None
    assert rows[kept["id"]].deleted_at is None
    [event] = events(factory, EventKind.ENTITY_DELETED)
    assert (event.plan_id, event.revision_id, event.entity_id) == (
        plan.id,
        plan.current_revision_id,
        uuid.UUID(gone["id"]),
    )
    assert event.before is not None and event.before["geometry"]["coords_m"] == [[1.0, 2.0]]
    assert (event.after, event.reason) == (None, "Duplicado")


def test_a_deleted_entity_cannot_be_edited_or_deleted_again(
    client: TestClient, revision_id: str
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))
    assert client.delete(f"{ENTITIES_URL}/{entity['id']}").status_code == 200

    assert (
        patch(client, entity["id"], operation="move_vertex", geometry=POLYLINE).status_code == 404
    )
    assert client.delete(f"{ENTITIES_URL}/{entity['id']}").status_code == 404


@pytest.mark.parametrize("operation", SNAP_OPERATIONS)
def test_every_snapping_operation_writes_an_event_with_its_name(
    client: TestClient, revision_id: str, factory: sessionmaker[Session], operation: str
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))

    response = patch(
        client, entity["id"], operation=operation, geometry=moved(POLYLINE, 0, [0, 0.5])
    )

    assert response.status_code == 200, response.text
    [event] = events(factory, EventKind.ENTITY_UPDATED)
    assert event.after is not None and event.after["operation"] == operation
    assert event.entity_id == uuid.UUID(entity["id"])


def test_create_sets_provenance_and_returns_the_entities_in_request_order(
    client: TestClient, revision_id: str, plan: Plan, owner: User, factory: sessionmaker[Session]
) -> None:
    items = [
        entity_body({"kind": "line", "coords_m": [[0, 0], [1.5, 0]]}),
        entity_body(POLYLINE),
        entity_body({"kind": "point", "coords_m": [[2, 1]]}, layer="L_EVIDENCE"),
        entity_body(
            {"kind": "text", "anchor_m": [1, 1], "height_mm_paper": 2.5, "content": "Calle"},
            layer="L_TEXT",
        ),
        entity_body(
            {"kind": "dimension", "p1_m": [0, 0], "p2_m": [3, 4], "declared_value_m": 5.02},
            layer="L_DIMENSIONS",
        ),
        entity_body(
            {
                "kind": "symbol",
                "anchor_m": [2, 1],
                "rotation_deg": 90,
                "symbol_id": "evidence_marker",
                "attributes": {"evidence_number": 1},
            },
            layer="L_EVIDENCE",
            attributes={"label": "Casquillo"},
        ),
    ]

    response = post_entities(client, revision_id, *items)

    assert response.status_code == 201, response.text
    created = response.json()["entities"]
    assert [e["type"] for e in created] == [
        "line",
        "polyline",
        "point",
        "text",
        "dimension",
        "symbol",
    ]
    for entity in created:
        assert entity["source"] == {"method": "manual", "job_id": None, "parent_entity_id": None}
        assert (entity["review_status"], entity["metric_origin"]) == ("accepted", "none")
        assert (entity["confidence"], entity["model_version"]) == (None, None)
        assert entity["created_by"] == str(owner.id)
        assert entity["revision_id"] == revision_id
        assert entity["revision_number"] == 1
        assert entity["deleted_at"] is None
    assert created[3]["geometry"]["rotation_deg"] == 0.0
    assert created[4]["geometry"]["measured_value_m"] == 5.0
    assert created[5]["geometry"]["attributes"] == {"evidence_number": 1}
    assert created[5]["attributes"] == {"label": "Casquillo"}
    assert list_ids(client, revision_id) == sorted(e["id"] for e in created)
    logged = events(factory, EventKind.ENTITY_CREATED)
    assert [str(e.entity_id) for e in logged] == [e["id"] for e in created]
    assert all((e.plan_id, e.revision_id) == (plan.id, plan.current_revision_id) for e in logged)
    assert all(e.before is None and e.after is not None for e in logged)
    assert logged[1].after is not None and logged[1].after["geometry"] == POLYLINE


def test_hand_drawn_entities_of_a_metric_scaled_drawing_are_scaled_drawing(
    client: TestClient, revision_id: str, plan: Plan, owner: User, factory: sessionmaker[Session]
) -> None:
    set_plan(factory, plan.id, source_mode=SourceMode.SCALED_DRAWING)
    with factory() as db:
        db.add(
            Calibration(plan_id=plan.id, method="scan_dpi", as_metric=True, accepted_by=owner.id)
        )
        db.commit()

    entity = create_one(client, revision_id, entity_body(POLYLINE))

    assert entity["metric_origin"] == "scaled_drawing"


@pytest.mark.parametrize(
    "item",
    [
        entity_body(POLYLINE, layer="L_UNKNOWN"),
        entity_body(POLYLINE, layer="L_SHEET"),
        entity_body({"kind": "symbol", "anchor_m": [0, 0], "symbol_id": "weapon"}),
        {**entity_body(POLYLINE), "type": "line"},
        entity_body({"kind": "line", "coords_m": [[0, 0], [1, 0], [2, 0]]}),
        entity_body({"kind": "polyline", "coords_m": [[0, 0]]}),
        entity_body({"kind": "polyline", "coords_m": [[0, 0], [1, 0]], "closed": True}),
        entity_body({"kind": "point", "coords_m": [[0, 0], [1, 0]]}),
        entity_body({"kind": "line", "coords_m": [["0", 0], [1, 0]]}),
        entity_body({"kind": "text", "anchor_m": [0, 0], "height_mm_paper": 0, "content": "A"}),
        entity_body({"kind": "text", "anchor_m": [0, 0], "height_mm_paper": 2, "content": ""}),
        entity_body({"kind": "dimension", "p1_m": [0, 0], "p2_m": [1, 0], "declared_value_m": 0}),
        entity_body({"kind": "circle", "center_m": [0, 0]}),
        {**entity_body(POLYLINE), "source": {"method": "cv"}},
        entity_body(POLYLINE, attributes={"label": {"nested": True}}),
    ],
)
def test_invalid_entities_are_422_and_write_nothing(
    client: TestClient, revision_id: str, factory: sessionmaker[Session], item: dict[str, Any]
) -> None:
    response = post_entities(client, revision_id, entity_body(POLYLINE), item)

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert entity_rows(factory) == []
    assert events(factory, EventKind.ENTITY_CREATED) == []


def test_non_finite_coordinates_are_422(
    client: TestClient, revision_id: str, factory: sessionmaker[Session]
) -> None:
    raw = (
        '{"entities": [{"type": "point", "layer": "L_EVIDENCE",'
        ' "geometry": {"kind": "point", "coords_m": [[NaN, 1.0]]}}]}'
    )

    response = client.post(
        f"{REVISIONS_URL}/{revision_id}/entities",
        content=raw,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422, response.text
    assert entity_rows(factory) == []


def test_an_empty_create_is_422(client: TestClient, revision_id: str) -> None:
    assert post_entities(client, revision_id).status_code == 422


def test_reclassify_and_relabel_change_layer_and_attributes(
    client: TestClient, revision_id: str, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))

    reclassified = patch(client, entity["id"], operation="reclassify", layer="L_ACCESS")
    relabeled = patch(client, entity["id"], operation="relabel", attributes={"label": "Puerta"})

    assert reclassified.status_code == 200, reclassified.text
    assert relabeled.status_code == 200, relabeled.text
    assert relabeled.json()["layer"] == "L_ACCESS"
    assert relabeled.json()["attributes"] == {"label": "Puerta"}
    assert relabeled.json()["geometry"] == POLYLINE
    logged = events(factory, EventKind.ENTITY_UPDATED)
    assert [e.after["operation"] for e in logged if e.after] == ["reclassify", "relabel"]
    assert logged[0].before is not None and logged[0].before["layer"] == "L_STRUCTURE"


@pytest.mark.parametrize(
    "body",
    [
        {"geometry": POLYLINE},
        {"operation": "fix_length", "geometry": POLYLINE},
        {"operation": "move_vertex"},
        {"operation": "move_vertex", "layer": "L_ACCESS"},
        {"operation": "reclassify"},
        {"operation": "relabel"},
        {"operation": "reclassify", "layer": "L_UNKNOWN"},
        {"operation": "reclassify", "layer": "L_SHEET"},
        {"operation": "move_vertex", "geometry": {"kind": "point", "coords_m": [[0, 0]]}},
        {"operation": "move_vertex", "geometry": POLYLINE, "source": {"method": "cv"}},
    ],
)
def test_invalid_patches_are_422_and_change_nothing(
    client: TestClient, revision_id: str, factory: sessionmaker[Session], body: dict[str, Any]
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))

    response = patch(client, entity["id"], **body)

    assert response.status_code == 422, response.text
    [row] = entity_rows(factory)
    assert (row.layer, row.geometry) == ("L_STRUCTURE", POLYLINE)
    assert events(factory, EventKind.ENTITY_UPDATED) == []


def freeze(factory: sessionmaker[Session], revision_id: str) -> None:
    with factory() as db:
        revision = db.get(Revision, uuid.UUID(revision_id))
        assert revision is not None
        revision.state = "frozen"
        revision.snapshot = {}
        revision.snapshot_sha256 = "0" * 64
        revision.frozen_at = datetime.now(UTC)
        db.commit()


def test_a_frozen_revision_is_read_only_with_409(
    client: TestClient, revision_id: str, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))
    freeze(factory, revision_id)

    responses = [
        post_entities(client, revision_id, entity_body(POLYLINE)),
        patch(client, entity["id"], operation="move_vertex", geometry=moved(POLYLINE, 1, [5, 0])),
        client.delete(f"{ENTITIES_URL}/{entity['id']}"),
    ]

    assert [r.status_code for r in responses] == [409, 409, 409]
    assert "frozen" in responses[0].json()["detail"]
    assert list_ids(client, revision_id) == [entity["id"]]
    assert len(events(factory, *EDIT_KINDS)) == 1


EDIT_KINDS = (EventKind.ENTITY_CREATED, EventKind.ENTITY_UPDATED, EventKind.ENTITY_DELETED)


def test_a_validated_plan_rejects_edits_with_409_v10(
    client: TestClient, revision_id: str, plan: Plan, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))
    set_plan(factory, plan.id, status=PlanStatus.VALIDATED)

    response = patch(client, entity["id"], operation="snap_grid", geometry=POLYLINE)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "V-10"
    assert response.json()["detail"] == "Este plano está validado. Reábrelo para editarlo."


@pytest.mark.parametrize(
    "status", [PlanStatus.CALIBRATION_PENDING, PlanStatus.REVIEW_PENDING, PlanStatus.REJECTED]
)
def test_plans_outside_editing_reject_new_entities_with_409(
    client: TestClient,
    revision_id: str,
    plan: Plan,
    factory: sessionmaker[Session],
    status: PlanStatus,
) -> None:
    set_plan(factory, plan.id, status=status)

    response = post_entities(client, revision_id, entity_body(POLYLINE))

    assert response.status_code == 409, response.text
    assert status.value in response.json()["detail"]
    assert entity_rows(factory) == []


def test_a_plan_in_review_accepts_edits(
    client: TestClient, revision_id: str, plan: Plan, factory: sessionmaker[Session]
) -> None:
    set_plan(factory, plan.id, status=PlanStatus.IN_REVIEW)

    assert post_entities(client, revision_id, entity_body(POLYLINE)).status_code == 201


def test_reader_may_list_but_not_edit_and_denials_are_audited(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    case_id: str,
    revision_id: str,
    factory: sessionmaker[Session],
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))
    reader = insert_user(factory)
    with factory() as db:
        db.add(CaseMember(case_file_id=uuid.UUID(case_id), user_id=reader.id, permissions=["read"]))
        db.commit()
    reader_client = signed_in(app, stack, reader)

    listed = reader_client.get(f"{REVISIONS_URL}/{revision_id}/entities")
    responses = [
        post_entities(reader_client, revision_id, entity_body(POLYLINE)),
        patch(reader_client, entity["id"], operation="move_vertex", geometry=POLYLINE),
        reader_client.delete(f"{ENTITIES_URL}/{entity['id']}"),
    ]

    assert listed.status_code == 200
    assert [r.status_code for r in responses] == [403, 403, 403]
    denied = events(factory, EventKind.ACCESS_DENIED)
    assert [(e.actor_id, e.after["route"] if e.after else None) for e in denied] == [
        (reader.id, f"{REVISIONS_URL}/{{revision_id}}/entities"),
        (reader.id, f"{ENTITIES_URL}/{{entity_id}}"),
        (reader.id, f"{ENTITIES_URL}/{{entity_id}}"),
    ]
    assert len(entity_rows(factory)) == 1


def test_non_member_cannot_list_entities(
    app: FastAPI, stack: ExitStack, revision_id: str, factory: sessionmaker[Session]
) -> None:
    stranger = signed_in(app, stack, insert_user(factory))

    response = stranger.get(f"{REVISIONS_URL}/{revision_id}/entities")

    assert response.status_code == 403
    assert len(events(factory, EventKind.ACCESS_DENIED)) == 1


def test_mutations_without_csrf_token_are_403(
    client: TestClient, revision_id: str, factory: sessionmaker[Session]
) -> None:
    entity = create_one(client, revision_id, entity_body(POLYLINE))
    del client.headers[CSRF_HEADER]

    responses = [
        post_entities(client, revision_id, entity_body(POLYLINE)),
        patch(client, entity["id"], operation="move_vertex", geometry=POLYLINE),
        client.delete(f"{ENTITIES_URL}/{entity['id']}"),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403]
    assert len(entity_rows(factory)) == 1


@pytest.mark.parametrize("missing", [str(uuid.uuid4()), "not-a-uuid"])
def test_unknown_or_malformed_ids_are_404(client: TestClient, missing: str) -> None:
    assert client.get(f"{REVISIONS_URL}/{missing}/entities").status_code == 404
    assert post_entities(client, missing, entity_body(POLYLINE)).status_code == 404
    assert patch(client, missing, operation="move_vertex", geometry=POLYLINE).status_code == 404
    assert client.delete(f"{ENTITIES_URL}/{missing}").status_code == 404


def test_anonymous_requests_are_401(app: FastAPI, stack: ExitStack) -> None:
    anonymous = stack.enter_context(TestClient(app, base_url="https://testserver"))

    assert anonymous.get(f"{REVISIONS_URL}/{uuid.uuid4()}/entities").status_code == 401
    assert anonymous.delete(f"{ENTITIES_URL}/{uuid.uuid4()}").status_code == 401
