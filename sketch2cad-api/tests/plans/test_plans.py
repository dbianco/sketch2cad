"""``POST /case-files/{id}/plans`` and ``GET /plans/{id}`` (REQ-03, US-04)."""

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
    CaseMember,
    Event,
    JobMeta,
    Plan,
    Revision,
    Source,
    User,
    UserRole,
)

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
SUGGEST_LINES = "suggest_lines"


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


def create_case(client: TestClient) -> str:
    body = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(CASES_URL, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def insert_source(factory: sessionmaker[Session], case_id: str, user_id: uuid.UUID) -> str:
    """A stored source row of ``case_id``; plan creation only reads its case file."""
    with factory() as db:
        source = Source(
            case_file_id=uuid.UUID(case_id),
            original_filename="croquis.png",
            media_type="image/png",
            byte_size=1024,
            sha256=uuid.uuid4().hex * 2,
            storage_key=f"sources/{uuid.uuid4().hex}",
            uploaded_by=user_id,
        )
        db.add(source)
        db.commit()
        return str(source.id)


@pytest.fixture
def owner(factory: sessionmaker[Session]) -> User:
    return insert_user(factory)


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    return signed_in(app, stack, owner)


@pytest.fixture
def case_id(client: TestClient) -> str:
    return create_case(client)


@pytest.fixture
def source_id(factory: sessionmaker[Session], case_id: str, owner: User) -> str:
    return insert_source(factory, case_id, owner.id)


def post_plan(client: TestClient, case_id: str, **body: Any) -> Any:
    return client.post(f"{CASES_URL}/{case_id}/plans", json={"title": "Plano general", **body})


def plan_events(factory: sessionmaker[Session]) -> list[Event]:
    with factory() as db:
        return list(db.scalars(select(Event).where(Event.kind == EventKind.PLAN_CREATED)))


def denied_events(factory: sessionmaker[Session]) -> list[Event]:
    with factory() as db:
        return list(db.scalars(select(Event).where(Event.kind == EventKind.ACCESS_DENIED)))


def count_plans(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return db.scalar(select(func.count()).select_from(Plan)) or 0


def test_plan_without_source_mode_is_schematic_sketch_pending_calibration(
    client: TestClient, case_id: str, source_id: str, factory: sessionmaker[Session]
) -> None:
    response = post_plan(client, case_id, primary_source_id=source_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_mode"] == "schematic_sketch"
    assert body["status"] == "CALIBRATION_PENDING"
    assert body["metric_status"] == "non_metric"
    assert (body["case_file_id"], body["primary_source_id"]) == (case_id, source_id)
    assert body["title"] == "Plano general"
    assert body["calibration"] is None
    assert body["lease"] is None
    assert body["validated_revision_id"] is None
    assert body["current_revision"]["number"] == 1
    assert body["current_revision"]["state"] == "draft"
    assert body["current_revision_id"] == body["current_revision"]["id"]
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(body["id"]))
        assert plan is not None
        assert plan.source_mode.value == "schematic_sketch"
        assert plan.status.value == "CALIBRATION_PENDING"
        revisions = list(db.scalars(select(Revision).where(Revision.plan_id == plan.id)))
    assert [(r.id, r.number, r.state, r.parent_revision_id) for r in revisions] == [
        (plan.current_revision_id, 1, "draft", None)
    ]


def test_plan_created_event_carries_plan_and_revision(
    client: TestClient, case_id: str, source_id: str, owner: User, factory: sessionmaker[Session]
) -> None:
    body = post_plan(client, case_id, primary_source_id=source_id).json()

    events = plan_events(factory)
    assert len(events) == 1
    event = events[0]
    assert event.case_file_id == uuid.UUID(case_id)
    assert event.plan_id == uuid.UUID(body["id"])
    assert event.revision_id == uuid.UUID(body["current_revision_id"])
    assert (event.actor_id, event.actor_role) == (owner.id, "technician")
    assert event.after == {
        "title": "Plano general",
        "primary_source_id": source_id,
        "source_mode": "schematic_sketch",
        "status": "CALIBRATION_PENDING",
        "metric_status": "non_metric",
        "revision_number": 1,
    }


def test_creating_a_plan_enqueues_no_suggest_lines_job(
    client: TestClient, case_id: str, source_id: str, factory: sessionmaker[Session]
) -> None:
    response = post_plan(client, case_id, primary_source_id=source_id)
    assert response.status_code == 201, response.text

    with factory() as db:
        suggest = db.scalar(
            select(func.count()).select_from(JobMeta).where(JobMeta.kind == SUGGEST_LINES)
        )
        plan_jobs = db.scalar(
            select(func.count())
            .select_from(JobMeta)
            .where(JobMeta.plan_id == uuid.UUID(response.json()["id"]))
        )
    assert suggest == 0
    assert plan_jobs == 0


def test_explicit_scaled_drawing_mode_is_stored(
    client: TestClient, case_id: str, source_id: str, factory: sessionmaker[Session]
) -> None:
    response = post_plan(client, case_id, primary_source_id=source_id, source_mode="scaled_drawing")

    assert response.status_code == 201, response.text
    assert response.json()["source_mode"] == "scaled_drawing"
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(response.json()["id"]))
        assert plan is not None and plan.source_mode.value == "scaled_drawing"


def test_primary_source_of_another_case_is_422_and_writes_nothing(
    client: TestClient, case_id: str, owner: User, factory: sessionmaker[Session]
) -> None:
    other_case = create_case(client)
    foreign_source = insert_source(factory, other_case, owner.id)

    response = post_plan(client, case_id, primary_source_id=foreign_source)

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert count_plans(factory) == 0
    assert plan_events(factory) == []


def test_unknown_primary_source_is_422(
    client: TestClient, case_id: str, factory: sessionmaker[Session]
) -> None:
    response = post_plan(client, case_id, primary_source_id=str(uuid.uuid4()))

    assert response.status_code == 422, response.text
    assert count_plans(factory) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"title": "   "},
        {"source_mode": "photo"},
        {"primary_source_id": "not-a-uuid"},
    ],
)
def test_invalid_body_is_422(
    client: TestClient,
    case_id: str,
    source_id: str,
    factory: sessionmaker[Session],
    body: dict[str, str],
) -> None:
    payload = {"title": "Plano", "primary_source_id": source_id, **body}

    response = client.post(f"{CASES_URL}/{case_id}/plans", json=payload)

    assert response.status_code == 422, response.text
    assert count_plans(factory) == 0


def test_create_without_csrf_token_is_403(
    client: TestClient, case_id: str, source_id: str, factory: sessionmaker[Session]
) -> None:
    del client.headers[CSRF_HEADER]

    response = post_plan(client, case_id, primary_source_id=source_id)

    assert response.status_code == 403
    assert count_plans(factory) == 0


def test_member_without_edit_gets_403_with_access_denied(
    app: FastAPI,
    stack: ExitStack,
    case_id: str,
    source_id: str,
    factory: sessionmaker[Session],
) -> None:
    reader = insert_user(factory)
    with factory() as db:
        db.add(CaseMember(case_file_id=uuid.UUID(case_id), user_id=reader.id, permissions=["read"]))
        db.commit()

    response = post_plan(signed_in(app, stack, reader), case_id, primary_source_id=source_id)

    assert response.status_code == 403
    assert count_plans(factory) == 0
    denied = denied_events(factory)
    assert [(e.case_file_id, e.actor_id) for e in denied] == [(uuid.UUID(case_id), reader.id)]
    assert denied[0].after == {
        "method": "POST",
        "route": f"{CASES_URL}/{{case_id}}/plans",
        "permission": "edit",
        "status": 403,
    }


def test_get_plan_returns_null_calibration_lease_and_draft_revision(
    client: TestClient, case_id: str, source_id: str
) -> None:
    created = post_plan(client, case_id, primary_source_id=source_id).json()

    response = client.get(f"{PLANS_URL}/{created['id']}")

    assert response.status_code == 200, response.text
    assert response.json() == created
    assert response.json()["lease"] is None
    assert response.json()["calibration"] is None


def test_get_plan_returns_only_the_active_calibration(
    client: TestClient,
    case_id: str,
    source_id: str,
    owner: User,
    factory: sessionmaker[Session],
) -> None:
    plan_id = uuid.UUID(post_plan(client, case_id, primary_source_id=source_id).json()["id"])
    matrix = [[0.5, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 1.0]]
    with factory() as db:
        db.add(Calibration(plan_id=plan_id, method="none", is_active=False, accepted_by=owner.id))
        db.flush()
        active = Calibration(
            plan_id=plan_id,
            method="scan_dpi",
            image_to_paper=matrix,
            reprojection_error_mm={"max": 0.2, "rms": 0.1},
            accepted_by=owner.id,
        )
        db.add(active)
        db.commit()
        active_id = str(active.id)

    response = client.get(f"{PLANS_URL}/{plan_id}")

    assert response.status_code == 200, response.text
    calibration = response.json()["calibration"]
    assert calibration["id"] == active_id
    assert calibration["method"] == "scan_dpi"
    assert calibration["image_to_paper"] == matrix
    assert calibration["reprojection_error_mm"] == {"max": 0.2, "rms": 0.1}
    assert calibration["control_points"] == []
    assert calibration["accepted_by"] == str(owner.id)


def test_get_plan_of_non_member_is_403_with_access_denied(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    case_id: str,
    source_id: str,
    factory: sessionmaker[Session],
) -> None:
    created = post_plan(client, case_id, primary_source_id=source_id).json()
    outsider = insert_user(factory)

    response = signed_in(app, stack, outsider).get(f"{PLANS_URL}/{created['id']}")

    assert response.status_code == 403
    assert created["title"] not in response.text
    denied = denied_events(factory)
    assert [(e.case_file_id, e.actor_id) for e in denied] == [(uuid.UUID(case_id), outsider.id)]
    assert denied[0].after == {
        "method": "GET",
        "route": f"{PLANS_URL}/{{plan_id}}",
        "permission": "read",
        "status": 403,
    }


@pytest.mark.parametrize("plan_id", [str(uuid.uuid4()), "not-a-uuid"])
def test_unknown_or_malformed_plan_is_404(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    response = client.get(f"{PLANS_URL}/{plan_id}")

    assert response.status_code == 404
    assert denied_events(factory) == []


def test_anonymous_plan_requests_are_401(app: FastAPI, stack: ExitStack) -> None:
    anonymous = stack.enter_context(TestClient(app, base_url="https://testserver"))

    assert anonymous.get(f"{PLANS_URL}/{uuid.uuid4()}").status_code == 401
    assert anonymous.post(f"{CASES_URL}/{uuid.uuid4()}/plans", json={}).status_code == 401
