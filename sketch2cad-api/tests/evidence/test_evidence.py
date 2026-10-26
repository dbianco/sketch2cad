"""Evidence register: numbering per case file, events, permissions and entity links (REQ-08)."""

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db.base import Base
from sketch2cad.db.models import CaseFile, Event, Evidence, Plan, PlanStatus, Source, User, UserRole
from sketch2cad.db.session import create_session_factory
from sketch2cad.evidence.service import create_evidence

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
EVIDENCE_URL = f"{API_V1_PREFIX}/evidence"
REVISIONS_URL = f"{API_V1_PREFIX}/revisions"
ENTITIES_URL = f"{API_V1_PREFIX}/entities"
POINT = {"kind": "point", "coords_m": [[2.1, 3.45]]}
MARKER = {
    "kind": "symbol",
    "anchor_m": [2.1, 3.45],
    "symbol_id": "evidence_marker",
    "attributes": {"evidence_number": 1},
}
LINE = {"kind": "line", "coords_m": [[0.0, 0.0], [1.0, 0.0]]}
# Time each creator holds the case file lock before committing, to force the race.
HOLD_SECONDS = 0.05


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


def new_case(client: TestClient) -> str:
    body = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def case_id(client: TestClient) -> str:
    return new_case(client)


def make_plan(client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User) -> str:
    """A plan in ``EDITING`` whose lease the owner holds; returns its draft revision id."""
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
    body = {"title": "Plano", "primary_source_id": source_id}
    response = client.post(f"{CASES_URL}/{case_id}/plans", json=body)
    assert response.status_code == 201, response.text
    plan_id = uuid.UUID(response.json()["id"])
    with factory() as db:
        plan = db.get(Plan, plan_id)
        assert plan is not None
        plan.status = PlanStatus.EDITING
        db.commit()
        revision_id = str(plan.current_revision_id)
    response = client.post(f"{API_V1_PREFIX}/plans/{plan_id}/lease")
    assert response.status_code == 200, response.text
    return revision_id


def post_evidence(client: TestClient, case_id: str, **body: Any) -> Any:
    return client.post(f"{CASES_URL}/{case_id}/evidence", json=body)


def create_one(client: TestClient, case_id: str, label: str = "Casquillo") -> dict[str, Any]:
    response = post_evidence(client, case_id, label=label)
    assert response.status_code == 201, response.text
    return response.json()


def numbers(client: TestClient, case_id: str) -> list[int]:
    response = client.get(f"{CASES_URL}/{case_id}/evidence")
    assert response.status_code == 200, response.text
    return [item["number"] for item in response.json()["items"]]


def add_member(client: TestClient, case_id: str, user: User, permissions: list[str]) -> None:
    body = {"user_id": str(user.id), "permissions": permissions}
    response = client.post(f"{CASES_URL}/{case_id}/members", json=body)
    assert response.status_code == 201, response.text


def entity_body(geometry: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": geometry["kind"], "layer": "L_EVIDENCE", "geometry": geometry, **extra}


def post_entity(client: TestClient, revision_id: str, item: dict[str, Any]) -> Any:
    return client.post(f"{REVISIONS_URL}/{revision_id}/entities", json={"entities": [item]})


def events_of(factory: sessionmaker[Session], kind: EventKind) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind == kind.value).order_by(Event.id)
        return list(db.scalars(query))


def test_create_assigns_sequential_numbers_and_lists_them(
    client: TestClient, case_id: str, owner: User
) -> None:
    first = create_one(client, case_id, "  Casquillo  ")
    assert first["number"] == 1
    assert first["label"] == "Casquillo"
    assert first["attributes"] == {}
    assert first["case_file_id"] == case_id
    assert first["created_by"] == str(owner.id)
    response = post_evidence(client, case_id, label="Mancha", attributes={"color": "rojo"})
    assert response.status_code == 201, response.text
    assert response.json()["number"] == 2
    listed = client.get(f"{CASES_URL}/{case_id}/evidence").json()["items"]
    assert [(e["number"], e["label"], e["attributes"]) for e in listed] == [
        (1, "Casquillo", {}),
        (2, "Mancha", {"color": "rojo"}),
    ]


def test_same_evidence_in_two_plans_shows_one_number_and_cases_repeat_numbers(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    """H3-9: the same evidence keeps its number in every plan; numbers restart per case."""
    create_one(client, case_id, "Vehículo A")
    evidence = create_one(client, case_id, "Casquillo")
    general = make_plan(client, factory, case_id, owner)
    detail = make_plan(client, factory, case_id, owner)
    placed_general = post_entity(client, general, entity_body(POINT, evidence_id=evidence["id"]))
    placed_detail = post_entity(client, detail, entity_body(MARKER, evidence_id=evidence["id"]))
    assert placed_general.status_code == 201, placed_general.text
    assert placed_detail.status_code == 201, placed_detail.text
    register = {
        e["id"]: e["number"] for e in client.get(f"{CASES_URL}/{case_id}/evidence").json()["items"]
    }
    shown: list[int] = []
    for revision_id in (general, detail):
        entities = client.get(f"{REVISIONS_URL}/{revision_id}/entities").json()["entities"]
        shown.extend(register[e["evidence_id"]] for e in entities if e["evidence_id"])
    assert shown == [2, 2]

    other_case = new_case(client)
    assert create_one(client, other_case, "Casquillo")["number"] == 1
    assert numbers(client, case_id) == [1, 2]
    assert numbers(client, other_case) == [1]


def run_concurrent_creations(
    database_url: str, test_schema: str, case_file_id: uuid.UUID, user_id: uuid.UUID, count: int
) -> list[int]:
    """Create ``count`` evidence items at once, each in its own thread and session."""
    engine = create_engine(
        database_url,
        pool_size=count,
        connect_args={"options": f"-csearch_path={test_schema}"},
    )
    sessions = create_session_factory(engine)
    barrier = threading.Barrier(count)
    results: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            with sessions() as db:
                case_file = db.get(CaseFile, case_file_id)
                user = db.get(User, user_id)
                assert case_file is not None and user is not None
                barrier.wait(timeout=30)
                evidence = create_evidence(db, case_file, user, "Indicio", {})
                time.sleep(HOLD_SECONDS)
                db.commit()
                with lock:
                    results.append(evidence.number)
        except BaseException as exc:  # noqa: BLE001 - reported by the test below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        engine.dispose()
    assert errors == []
    return sorted(results)


@pytest.mark.parametrize("count", [2, 20])
def test_concurrent_creations_never_share_a_number(
    client: TestClient,
    factory: sessionmaker[Session],
    database_url: str,
    test_schema: str,
    case_id: str,
    owner: User,
    count: int,
) -> None:
    got = run_concurrent_creations(database_url, test_schema, uuid.UUID(case_id), owner.id, count)
    assert got == list(range(1, count + 1))
    assert numbers(client, case_id) == list(range(1, count + 1))
    assert len(events_of(factory, EventKind.EVIDENCE_CREATED)) == count


def test_numbers_are_immutable_and_never_reused(client: TestClient, case_id: str) -> None:
    created = [create_one(client, case_id, f"Indicio {n}") for n in range(3)]
    middle = created[1]
    response = client.patch(f"{EVIDENCE_URL}/{middle['id']}", json={"label": "Arma blanca"})
    assert response.status_code == 200, response.text
    assert response.json()["number"] == 2
    assert response.json()["label"] == "Arma blanca"
    rejected = client.patch(f"{EVIDENCE_URL}/{middle['id']}", json={"number": 9})
    assert rejected.status_code == 422
    assert create_one(client, case_id)["number"] == 4
    assert numbers(client, case_id) == [1, 2, 3, 4]


def test_events_record_creation_and_update_with_before_and_after(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    response = post_evidence(client, case_id, label="Casquillo", attributes={"calibre": "9mm"})
    evidence = response.json()
    patch = {"label": "Casquillo percutido", "attributes": {"calibre": "9mm", "n": 2}}
    assert client.patch(f"{EVIDENCE_URL}/{evidence['id']}", json=patch).status_code == 200
    (created,) = events_of(factory, EventKind.EVIDENCE_CREATED)
    (updated,) = events_of(factory, EventKind.EVIDENCE_UPDATED)
    for event in (created, updated):
        assert event.case_file_id == uuid.UUID(case_id)
        assert event.plan_id is None and event.revision_id is None
        assert event.entity_id == uuid.UUID(evidence["id"])
        assert event.actor_id == owner.id
    first = {"number": 1, "label": "Casquillo", "attributes": {"calibre": "9mm"}}
    assert created.before is None and created.after == first
    assert updated.before == first
    assert updated.after == {"number": 1, **patch}
    with factory() as db:
        row = db.get(Evidence, uuid.UUID(evidence["id"]))
        assert row is not None and row.attributes == patch["attributes"]


def test_patch_keeps_the_fields_it_does_not_send(client: TestClient, case_id: str) -> None:
    response = post_evidence(client, case_id, label="Casquillo", attributes={"a": 1})
    evidence_id = response.json()["id"]
    renamed = client.patch(f"{EVIDENCE_URL}/{evidence_id}", json={"label": "Vaina"})
    assert renamed.json()["attributes"] == {"a": 1}
    cleared = client.patch(f"{EVIDENCE_URL}/{evidence_id}", json={"attributes": {}})
    assert (cleared.json()["label"], cleared.json()["attributes"]) == ("Vaina", {})


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"label": ""},
        {"label": "   "},
        {"label": 7},
        {"label": "Casquillo", "number": 1},
        {"label": "Casquillo", "attributes": {"nested": {"a": 1}}},
        {"label": "Casquillo", "attributes": {"list": [1, 2]}},
        {"label": "Casquillo", "attributes": {"Bad-Name": 1}},
    ],
)
def test_invalid_creation_is_rejected(client: TestClient, case_id: str, body: Any) -> None:
    response = post_evidence(client, case_id, **body)
    assert response.status_code == 422, response.text
    assert numbers(client, case_id) == []


@pytest.mark.parametrize(
    "body", [{}, {"label": " "}, {"label": None}, {"attributes": {"x": {"y": 1}}}]
)
def test_invalid_patch_is_rejected(client: TestClient, case_id: str, body: Any) -> None:
    evidence = create_one(client, case_id)
    response = client.patch(f"{EVIDENCE_URL}/{evidence['id']}", json=body)
    assert response.status_code == 422, response.text


def test_read_only_member_lists_but_cannot_create_or_edit(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    factory: sessionmaker[Session],
    case_id: str,
) -> None:
    evidence = create_one(client, case_id)
    reader = insert_user(factory)
    add_member(client, case_id, reader, ["read"])
    reader_client = signed_in(app, stack, reader)
    assert reader_client.get(f"{CASES_URL}/{case_id}/evidence").status_code == 200
    assert post_evidence(reader_client, case_id, label="Otro").status_code == 403
    patch = reader_client.patch(f"{EVIDENCE_URL}/{evidence['id']}", json={"label": "Otro"})
    assert patch.status_code == 403
    denied = events_of(factory, EventKind.ACCESS_DENIED)
    assert [(e.actor_id, (e.after or {})["permission"]) for e in denied] == [
        (reader.id, "edit"),
        (reader.id, "edit"),
    ]
    assert numbers(client, case_id) == [1]
    assert events_of(factory, EventKind.EVIDENCE_UPDATED) == []


def test_non_member_is_denied_and_unknown_ids_are_not_found(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    factory: sessionmaker[Session],
    case_id: str,
) -> None:
    evidence = create_one(client, case_id)
    stranger = signed_in(app, stack, insert_user(factory))
    assert stranger.get(f"{CASES_URL}/{case_id}/evidence").status_code == 403
    assert stranger.patch(f"{EVIDENCE_URL}/{evidence['id']}", json={"label": "x"}).status_code == (
        403
    )
    denied = events_of(factory, EventKind.ACCESS_DENIED)
    assert [(e.after or {})["permission"] for e in denied] == ["read", "edit"]
    assert client.get(f"{CASES_URL}/{uuid.uuid4()}/evidence").status_code == 404
    for evidence_id in (uuid.uuid4(), "not-a-uuid"):
        response = client.patch(f"{EVIDENCE_URL}/{evidence_id}", json={"label": "x"})
        assert response.status_code == 404


def test_mutations_require_csrf(client: TestClient, case_id: str) -> None:
    evidence = create_one(client, case_id)
    del client.headers[CSRF_HEADER]
    assert post_evidence(client, case_id, label="x").status_code == 403
    assert client.patch(f"{EVIDENCE_URL}/{evidence['id']}", json={"label": "x"}).status_code == (
        403
    )


def test_entity_may_not_reference_evidence_of_another_case(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    revision_id = make_plan(client, factory, case_id, owner)
    foreign = create_one(client, new_case(client))
    sent = [foreign["id"], str(uuid.uuid4())]
    responses = [
        post_entity(client, revision_id, entity_body(POINT, evidence_id=evidence_id))
        for evidence_id in sent
    ]
    assert [r.status_code for r in responses] == [422, 422]
    # Evidence of another case reads exactly like a missing one: nothing leaks across cases.
    details = {r.json()["detail"].replace(e, "<id>") for r, e in zip(responses, sent, strict=True)}
    assert details == {"entities[0]: Evidence <id> is not in this case file."}
    own = create_one(client, case_id)
    line = post_entity(client, revision_id, entity_body(LINE, evidence_id=own["id"]))
    assert line.status_code == 422
    with factory() as db:
        assert db.scalars(select(Event).where(Event.kind == "entity.created")).all() == []


def test_relabel_links_changes_and_clears_the_evidence(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    revision_id = make_plan(client, factory, case_id, owner)
    own = create_one(client, case_id)
    foreign = create_one(client, new_case(client))
    response = post_entity(client, revision_id, entity_body(POINT))
    entity_id = response.json()["entities"][0]["id"]
    url = f"{ENTITIES_URL}/{entity_id}"

    linked = client.patch(url, json={"operation": "relabel", "evidence_id": own["id"]})
    assert linked.status_code == 200, linked.text
    assert linked.json()["evidence_id"] == own["id"]
    crossed = client.patch(url, json={"operation": "relabel", "evidence_id": foreign["id"]})
    assert crossed.status_code == 422
    moved = {"operation": "move_vertex", "geometry": POINT, "evidence_id": own["id"]}
    assert client.patch(url, json=moved).status_code == 422
    cleared = client.patch(url, json={"operation": "relabel", "evidence_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["evidence_id"] is None

    updates = events_of(factory, EventKind.ENTITY_UPDATED)
    assert [(e.before or {})["evidence_id"] for e in updates] == [None, own["id"]]
    assert [(e.after or {})["evidence_id"] for e in updates] == [own["id"], None]


def test_marker_number_follows_the_evidence_on_create_and_relabel(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    """A marker's ``evidence_number`` attribute shows the register's number, never a stale one."""
    revision_id = make_plan(client, factory, case_id, owner)
    create_one(client, case_id, "Vehículo A")
    second = create_one(client, case_id, "Casquillo")
    placed = post_entity(client, revision_id, entity_body(MARKER, evidence_id=second["id"]))
    assert placed.status_code == 201, placed.text
    assert placed.json()["entities"][0]["geometry"]["attributes"] == {"evidence_number": 2}

    response = post_entity(client, revision_id, entity_body(MARKER))
    marker = response.json()["entities"][0]
    assert marker["geometry"]["attributes"] == {"evidence_number": 1}
    third = create_one(client, case_id, "Mancha")
    url = f"{ENTITIES_URL}/{marker['id']}"
    linked = client.patch(url, json={"operation": "relabel", "evidence_id": third["id"]})
    assert linked.status_code == 200, linked.text
    assert linked.json()["geometry"]["attributes"] == {"evidence_number": 3}
    assert linked.json()["geometry"]["anchor_m"] == MARKER["anchor_m"]
    stale = {**MARKER, "attributes": {"evidence_number": 9, "label": "x"}}
    edited = client.patch(url, json={"operation": "relabel", "geometry": stale})
    assert edited.status_code == 200, edited.text
    assert edited.json()["geometry"]["attributes"] == {"evidence_number": 3, "label": "x"}

    after = (events_of(factory, EventKind.ENTITY_UPDATED)[0].after or {})["geometry"]
    assert after["attributes"] == {"evidence_number": 3}
    point = post_entity(client, revision_id, entity_body(POINT, evidence_id=third["id"]))
    assert point.json()["entities"][0]["geometry"] == POINT
