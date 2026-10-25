"""Plan state machine: the section 9.2 table, roles, reasons and effects (REQ-10, US-14,
V-09, V-10, H2-7)."""

import itertools
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
    CaseMember,
    Event,
    MetricStatus,
    Plan,
    PlanStatus,
    Revision,
    Source,
    User,
    UserRole,
)
from sketch2cad.plans.transitions import TRANSITIONS, allowed_targets

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
POLYLINE = {"kind": "polyline", "coords_m": [[0.0, 0.0], [4.2, 0.0]], "closed": False}
MOVED = {"kind": "polyline", "coords_m": [[0.0, 0.0], [5.0, 0.0]], "closed": False}
V09_VALIDATE = "Solo un revisor puede validar este plano."
V09_REASON = "Indica el motivo del rechazo."
V10 = "Este plano está validado. Reábrelo para editarlo."

S = PlanStatus
SPEC_TABLE = {
    (S.CREATED, S.CALIBRATION_PENDING),
    (S.CALIBRATION_PENDING, S.EDITING),
    (S.EDITING, S.REVIEW_PENDING),
    (S.REVIEW_PENDING, S.IN_REVIEW),
    (S.IN_REVIEW, S.VALIDATED),
    (S.IN_REVIEW, S.REJECTED),
    (S.REJECTED, S.EDITING),
    (S.VALIDATED, S.EXPORTED),
    (S.VALIDATED, S.REOPENED),
    (S.EXPORTED, S.REOPENED),
    (S.REOPENED, S.EDITING),
}
REQUESTABLE = {
    (S.EDITING, S.REVIEW_PENDING),
    (S.REVIEW_PENDING, S.IN_REVIEW),
    (S.IN_REVIEW, S.VALIDATED),
    (S.IN_REVIEW, S.REJECTED),
    (S.VALIDATED, S.REOPENED),
    (S.EXPORTED, S.REOPENED),
}


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


def insert_user(factory: sessionmaker[Session], role: UserRole) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name=f"{role.value.title()} {uuid.uuid4().hex[:4]}",
            password_hash=hash_password(PASSWORD),
            role=role,
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
    return insert_user(factory, UserRole.TECHNICIAN)


@pytest.fixture
def client(app: FastAPI, stack: ExitStack, owner: User) -> TestClient:
    """The technician who created the case file and the plan."""
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
        plan.metric_status = MetricStatus.APPROXIMATE
        db.add(Calibration(plan_id=plan.id, method="scan_dpi", accepted_by=owner.id))
        db.commit()
    return plan_id


def member(
    app: FastAPI,
    stack: ExitStack,
    factory: sessionmaker[Session],
    plan_id: str,
    role: UserRole,
    permissions: list[str],
) -> tuple[User, TestClient]:
    """A new user with ``role`` and ``permissions`` on the plan's case file, signed in."""
    user = insert_user(factory, role)
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        db.add(CaseMember(case_file_id=plan.case_file_id, user_id=user.id, permissions=permissions))
        db.commit()
    return user, signed_in(app, stack, user)


@pytest.fixture
def reviewer(
    app: FastAPI, stack: ExitStack, factory: sessionmaker[Session], plan_id: str
) -> tuple[User, TestClient]:
    return member(app, stack, factory, plan_id, UserRole.REVIEWER, ["read", "edit", "deliver"])


def move(client: TestClient, plan_id: str, to: str, **extra: Any) -> Any:
    return client.post(f"{PLANS_URL}/{plan_id}/transition", json={"to": to, **extra})


def set_status(factory: sessionmaker[Session], plan_id: str, status: PlanStatus) -> None:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        plan.status = status
        db.commit()


def stored_plan(factory: sessionmaker[Session], plan_id: str) -> Plan:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        return plan


def events(factory: sessionmaker[Session], kind: EventKind) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind == kind.value).order_by(Event.id)
        return list(db.scalars(query))


def steps(factory: sessionmaker[Session]) -> list[tuple[str, str]]:
    return [
        (event.before["status"], event.after["status"])
        for event in events(factory, EventKind.PLAN_TRANSITION)
        if event.before is not None and event.after is not None
    ]


def denied_permissions(factory: sessionmaker[Session]) -> list[Any]:
    return [(e.after or {}).get("permission") for e in events(factory, EventKind.ACCESS_DENIED)]


def add_entity(client: TestClient, plan_id: str) -> str:
    revision_id = client.get(f"{PLANS_URL}/{plan_id}").json()["current_revision_id"]
    body = {"entities": [{"type": "polyline", "layer": "L_STRUCTURE", "geometry": POLYLINE}]}
    response = client.post(f"{API_V1_PREFIX}/revisions/{revision_id}/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()["entities"][0]["id"]


def validated_plan(
    client: TestClient, reviewer_client: TestClient, plan_id: str
) -> tuple[str, str]:
    """Drive the plan to ``VALIDATED`` through the API; returns (entity id, revision id)."""
    assert client.post(f"{PLANS_URL}/{plan_id}/lease").status_code == 200
    entity_id = add_entity(client, plan_id)
    for actor, to in [
        (client, "REVIEW_PENDING"),
        (reviewer_client, "IN_REVIEW"),
        (reviewer_client, "VALIDATED"),
    ]:
        response = move(actor, plan_id, to)
        assert response.status_code == 200, response.text
    return entity_id, response.json()["validated_revision_id"]


def test_the_table_is_spec_section_9_2() -> None:
    assert set(TRANSITIONS) == SPEC_TABLE
    assert {pair for pair, rule in TRANSITIONS.items() if rule.requestable} == REQUESTABLE
    assert allowed_targets(S.IN_REVIEW) == [S.VALIDATED, S.REJECTED]
    assert TRANSITIONS[(S.IN_REVIEW, S.REJECTED)].reason_required
    assert not any(
        rule.reason_required for pair, rule in TRANSITIONS.items() if pair[1] != S.REJECTED
    )


def test_every_pair_outside_the_table_returns_409_over_the_full_9x9_matrix(
    plan_id: str, reviewer: tuple[User, TestClient], factory: sessionmaker[Session]
) -> None:
    _, reviewer_client = reviewer
    checked: list[tuple[PlanStatus, PlanStatus]] = []
    for current, to in itertools.product(PlanStatus, repeat=2):
        if (current, to) in REQUESTABLE:
            continue  # exercised with their effects by the tests below
        set_status(factory, plan_id, current)

        response = move(reviewer_client, plan_id, to.value, reason="motivo")

        assert response.status_code == 409, (current, to, response.text)
        expected = "system_transition" if (current, to) in SPEC_TABLE else "illegal_transition"
        assert response.json()["code"] == expected, (current, to)
        assert response.json()["allowed"] == [t.value for t in allowed_targets(current)]
        assert stored_plan(factory, plan_id).status is current
        checked.append((current, to))

    assert len(checked) == 81 - len(REQUESTABLE)
    assert all((status, status) in checked for status in PlanStatus)
    assert events(factory, EventKind.PLAN_TRANSITION) == []


def test_review_validate_and_reopen_apply_the_section_9_2_effects(
    client: TestClient,
    owner: User,
    plan_id: str,
    reviewer: tuple[User, TestClient],
    factory: sessionmaker[Session],
) -> None:
    reviewer_user, reviewer_client = reviewer
    assert client.post(f"{PLANS_URL}/{plan_id}/lease").status_code == 200
    add_entity(client, plan_id)

    submitted = move(client, plan_id, "REVIEW_PENDING")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "REVIEW_PENDING"
    assert submitted.json()["lease"] is None

    taken = move(reviewer_client, plan_id, "IN_REVIEW")
    assert taken.status_code == 200, taken.text
    assert taken.json()["lease"]["user_id"] == str(reviewer_user.id)

    validated = move(reviewer_client, plan_id, "VALIDATED")
    assert validated.status_code == 200, validated.text
    revision_1 = validated.json()["current_revision_id"]
    assert validated.json()["validated_revision_id"] == revision_1
    assert validated.json()["current_revision"]["state"] == "frozen"
    with factory() as db:
        frozen = db.get(Revision, uuid.UUID(revision_1))
        assert frozen is not None and frozen.snapshot_sha256 is not None
        digest = frozen.snapshot_sha256

    reopened = move(reviewer_client, plan_id, "REOPENED")
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()
    assert body["status"] == "EDITING"
    assert body["current_revision"]["number"] == 2
    assert body["current_revision"]["state"] == "draft"
    assert body["validated_revision_id"] == revision_1
    assert body["metric_status"] == "approximate"
    with factory() as db:
        child = db.get(Revision, uuid.UUID(body["current_revision_id"]))
        parent = db.get(Revision, uuid.UUID(revision_1))
        assert child is not None and parent is not None
        assert child.parent_revision_id == parent.id
        assert parent.snapshot_sha256 == digest

    assert steps(factory) == [
        ("EDITING", "REVIEW_PENDING"),
        ("REVIEW_PENDING", "IN_REVIEW"),
        ("IN_REVIEW", "VALIDATED"),
        ("VALIDATED", "REOPENED"),
        ("REOPENED", "EDITING"),
    ]
    transitions = events(factory, EventKind.PLAN_TRANSITION)
    assert [e.actor_id for e in transitions] == [owner.id] + [reviewer_user.id] * 4
    assert transitions[2].revision_id == uuid.UUID(revision_1)
    assert transitions[2].after == {"status": "VALIDATED", "validated_revision_id": revision_1}
    assert [e.actor_id for e in events(factory, EventKind.LEASE_RELEASED)] == [owner.id]
    assert len(events(factory, EventKind.REVISION_FROZEN)) == 1
    assert len(events(factory, EventKind.REVISION_REOPENED)) == 1


def test_reopening_an_exported_plan_creates_the_next_draft(
    client: TestClient,
    plan_id: str,
    reviewer: tuple[User, TestClient],
    factory: sessionmaker[Session],
) -> None:
    _, reviewer_client = reviewer
    validated_plan(client, reviewer_client, plan_id)
    set_status(factory, plan_id, PlanStatus.EXPORTED)

    response = move(reviewer_client, plan_id, "REOPENED")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "EDITING"
    assert response.json()["current_revision"]["number"] == 2


def test_a_technician_cannot_validate_403_v09_and_access_denied(
    client: TestClient, owner: User, plan_id: str, factory: sessionmaker[Session]
) -> None:
    set_status(factory, plan_id, PlanStatus.IN_REVIEW)

    response = move(client, plan_id, "VALIDATED")

    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["detail"] == V09_VALIDATE
    assert response.json()["code"] == "V-09"
    plan = stored_plan(factory, plan_id)
    assert plan.status is PlanStatus.IN_REVIEW
    assert plan.validated_revision_id is None
    denied = events(factory, EventKind.ACCESS_DENIED)
    assert len(denied) == 1
    assert denied[0].actor_id == owner.id
    assert denied[0].after == {
        "method": "POST",
        "route": f"{PLANS_URL}/{{plan_id}}/transition",
        "permission": "role:reviewer",
        "status": 403,
    }
    assert events(factory, EventKind.PLAN_TRANSITION) == []
    assert events(factory, EventKind.REVISION_FROZEN) == []


@pytest.mark.parametrize(
    ("current", "to"),
    [
        (PlanStatus.REVIEW_PENDING, "IN_REVIEW"),
        (PlanStatus.IN_REVIEW, "REJECTED"),
        (PlanStatus.VALIDATED, "REOPENED"),
        (PlanStatus.EXPORTED, "REOPENED"),
    ],
)
def test_a_technician_cannot_take_reject_or_reopen(
    client: TestClient,
    plan_id: str,
    factory: sessionmaker[Session],
    current: PlanStatus,
    to: str,
) -> None:
    set_status(factory, plan_id, current)

    response = move(client, plan_id, to, reason="motivo")

    assert response.status_code == 403, response.text
    assert "code" not in response.json()
    assert stored_plan(factory, plan_id).status is current
    assert len(events(factory, EventKind.ACCESS_DENIED)) == 1


def test_an_admin_member_cannot_submit_for_review(
    app: FastAPI, stack: ExitStack, plan_id: str, factory: sessionmaker[Session]
) -> None:
    _, admin_client = member(app, stack, factory, plan_id, UserRole.ADMIN, ["read", "edit"])

    response = move(admin_client, plan_id, "REVIEW_PENDING")

    assert response.status_code == 403, response.text
    assert stored_plan(factory, plan_id).status is PlanStatus.EDITING
    assert denied_permissions(factory) == ["role:technician"]


def test_a_reviewer_without_edit_gets_403_with_access_denied(
    app: FastAPI, stack: ExitStack, plan_id: str, factory: sessionmaker[Session]
) -> None:
    set_status(factory, plan_id, PlanStatus.IN_REVIEW)
    _, reader = member(app, stack, factory, plan_id, UserRole.REVIEWER, ["read"])

    response = move(reader, plan_id, "VALIDATED")

    assert response.status_code == 403, response.text
    assert stored_plan(factory, plan_id).status is PlanStatus.IN_REVIEW
    assert denied_permissions(factory) == ["edit"]


@pytest.mark.parametrize("extra", [{}, {"reason": None}, {"reason": ""}, {"reason": "   "}])
def test_a_rejection_without_reason_returns_422_v09(
    plan_id: str,
    reviewer: tuple[User, TestClient],
    factory: sessionmaker[Session],
    extra: dict[str, Any],
) -> None:
    _, reviewer_client = reviewer
    set_status(factory, plan_id, PlanStatus.IN_REVIEW)

    response = move(reviewer_client, plan_id, "REJECTED", **extra)

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == V09_REASON
    assert response.json()["code"] == "V-09"
    assert stored_plan(factory, plan_id).status is PlanStatus.IN_REVIEW
    assert events(factory, EventKind.PLAN_TRANSITION) == []


def test_a_rejection_with_reason_returns_the_plan_to_editing(
    plan_id: str, reviewer: tuple[User, TestClient], factory: sessionmaker[Session]
) -> None:
    _, reviewer_client = reviewer
    set_status(factory, plan_id, PlanStatus.IN_REVIEW)

    response = move(reviewer_client, plan_id, "REJECTED", reason="  Falta la cota del cruce. ")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "EDITING"
    assert response.json()["current_revision"]["state"] == "draft"
    assert steps(factory) == [("IN_REVIEW", "REJECTED"), ("REJECTED", "EDITING")]
    assert [e.reason for e in events(factory, EventKind.PLAN_TRANSITION)] == [
        "Falta la cota del cruce.",
        None,
    ]


def test_a_rejection_releases_the_reviewer_lease(
    plan_id: str, reviewer: tuple[User, TestClient], factory: sessionmaker[Session]
) -> None:
    _, reviewer_client = reviewer
    set_status(factory, plan_id, PlanStatus.IN_REVIEW)
    csrf = reviewer_client.cookies.get("s2c_csrf") or ""
    taken = reviewer_client.post(f"/api/v1/plans/{plan_id}/lease", headers={"X-CSRF-Token": csrf})
    assert taken.status_code == 200, taken.text

    response = move(reviewer_client, plan_id, "REJECTED", reason="Falta la cota.")

    assert response.status_code == 200, response.text
    assert response.json()["lease"] is None
    assert len(events(factory, EventKind.LEASE_RELEASED)) == 1


def test_patch_entity_on_a_validated_plan_returns_409_v10(
    client: TestClient,
    plan_id: str,
    reviewer: tuple[User, TestClient],
    factory: sessionmaker[Session],
) -> None:
    _, reviewer_client = reviewer
    entity_id, revision_id = validated_plan(client, reviewer_client, plan_id)

    for actor in (client, reviewer_client):
        response = actor.patch(
            f"{API_V1_PREFIX}/entities/{entity_id}",
            json={"operation": "move_vertex", "geometry": MOVED},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == V10
        assert response.json()["code"] == "V-10"
    assert events(factory, EventKind.ENTITY_UPDATED) == []
    assert stored_plan(factory, plan_id).validated_revision_id == uuid.UUID(revision_id)


def test_submitting_while_another_user_edits_returns_409_v12(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    plan_id: str,
    factory: sessionmaker[Session],
) -> None:
    _, other = member(app, stack, factory, plan_id, UserRole.TECHNICIAN, ["read", "edit"])
    assert other.post(f"{PLANS_URL}/{plan_id}/lease").status_code == 200

    response = move(client, plan_id, "REVIEW_PENDING")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "V-12"
    assert stored_plan(factory, plan_id).status is PlanStatus.EDITING
    assert events(factory, EventKind.PLAN_TRANSITION) == []


def test_validating_a_plan_whose_revision_is_already_frozen_returns_409(
    client: TestClient,
    plan_id: str,
    reviewer: tuple[User, TestClient],
    factory: sessionmaker[Session],
) -> None:
    _, reviewer_client = reviewer
    validated_plan(client, reviewer_client, plan_id)
    set_status(factory, plan_id, PlanStatus.IN_REVIEW)

    response = move(reviewer_client, plan_id, "VALIDATED")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "revision_conflict"


def test_transition_requires_csrf_and_a_known_status(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    token = client.headers.pop(CSRF_HEADER)
    assert move(client, plan_id, "REVIEW_PENDING").status_code == 403
    client.headers[CSRF_HEADER] = token
    assert move(client, plan_id, "DONE").status_code == 422
    assert move(client, plan_id, "REVIEW_PENDING", extra=1).status_code == 422
    assert (
        client.post(f"{PLANS_URL}/{uuid.uuid4()}/transition", json={"to": "EDITING"}).status_code
        == 404
    )
    assert stored_plan(factory, plan_id).status is PlanStatus.EDITING
