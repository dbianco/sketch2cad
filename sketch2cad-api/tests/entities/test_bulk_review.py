"""Bulk review of suggestions and metric-origin promotion (REQ-17, US-12, CA-05, H5-4/8/10)."""

import ast
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
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
from sketch2cad.audit.canonical import event_content
from sketch2cad.audit.verify import verify_chain
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
from sketch2cad.worker.tasks_suggest import locking_entities_exist

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
REVISIONS_URL = f"{API_V1_PREFIX}/revisions"
ENTITIES_URL = f"{API_V1_PREFIX}/entities"
POLYLINE = {"kind": "polyline", "coords_m": [[0.0, 0.0], [4.2, 0.0], [4.2, 3.1]], "closed": False}
SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "sketch2cad"


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


def set_plan(factory: sessionmaker[Session], plan_id: uuid.UUID, **values: Any) -> None:
    with factory() as db:
        plan = db.get(Plan, plan_id)
        assert plan is not None
        for name, value in values.items():
            setattr(plan, name, value)
        db.commit()


def make_plan(
    client: TestClient,
    factory: sessionmaker[Session],
    owner: User,
    mode: SourceMode = SourceMode.SCALED_DRAWING,
    as_metric: bool = True,
    case_id: str | None = None,
) -> Plan:
    """A calibrated plan in ``EDITING`` with its draft revision 1 and the owner's lease."""
    case_id = case_id or new_case(client)
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
    set_plan(factory, plan_id, status=PlanStatus.EDITING, source_mode=mode)
    with factory() as db:
        calibration = Calibration(
            plan_id=plan_id, method="scan_dpi", as_metric=as_metric, accepted_by=owner.id
        )
        db.add(calibration)
        db.commit()
    assert client.post(f"{API_V1_PREFIX}/plans/{plan_id}/lease").status_code == 200
    with factory() as db:
        plan = db.get(Plan, plan_id)
        assert plan is not None
        return plan


@pytest.fixture
def plan(client: TestClient, factory: sessionmaker[Session], owner: User) -> Plan:
    return make_plan(client, factory, owner)


def add_suggestions(
    factory: sessionmaker[Session], revision_id: object, count: int, layer: str = "L_STRUCTURE"
) -> list[str]:
    """Insert ``count`` pending ``cv`` suggestions as the ``suggest_lines`` job does."""
    job_id = str(uuid.uuid4())
    with factory() as db:
        rows = [
            Entity(
                revision_id=revision_id,
                type="polyline",
                layer=layer,
                geometry=POLYLINE,
                source={"method": "cv", "job_id": job_id, "parent_entity_id": None},
                confidence=0.75,
                model_version="vision-1",
                review_status="suggested",
                metric_origin="trace_only",
            )
            for _ in range(count)
        ]
        db.add_all(rows)
        db.commit()
        return sorted(str(row.id) for row in rows)


def review(client: TestClient, revision_id: object, ids: list[str], status: str) -> Any:
    body = {"ids": ids, "review_status": status}
    return client.post(f"{REVISIONS_URL}/{revision_id}/entities:bulk-review", json=body)


def rows(factory: sessionmaker[Session]) -> dict[str, Entity]:
    with factory() as db:
        return {str(row.id): row for row in db.scalars(select(Entity))}


def events(factory: sessionmaker[Session], *kinds: EventKind) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind.in_([kind.value for kind in kinds]))
        return list(db.scalars(query.order_by(Event.id)))


def plan_status(factory: sessionmaker[Session], plan_id: uuid.UUID) -> str:
    with factory() as db:
        row = db.get(Plan, plan_id)
        assert row is not None
        return row.metric_status.value


@pytest.mark.parametrize("status", ["accepted", "rejected"])
def test_reviewing_100_ids_changes_100_rows_and_writes_100_events(
    client: TestClient, plan: Plan, owner: User, factory: sessionmaker[Session], status: str
) -> None:
    """H5-4: one transaction, one ``entity.reviewed`` per entity; the chain still verifies."""
    ids = add_suggestions(factory, plan.current_revision_id, 100)

    response = review(client, plan.current_revision_id, ids, status)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["review_status"], body["changed"], body["unchanged"]) == (status, 100, 0)
    assert body["changed_ids"] == ids and body["unchanged_ids"] == []
    stored = rows(factory)
    assert {row.review_status for row in stored.values()} == {status}
    assert all((row.deleted_at is not None) == (status == "rejected") for row in stored.values())
    logged = events(factory, EventKind.ENTITY_REVIEWED)
    assert [str(e.entity_id) for e in logged] == ids
    assert {(e.actor_id, e.plan_id, e.revision_id) for e in logged} == {
        (owner.id, plan.id, plan.current_revision_id)
    }
    origin = "scaled_drawing" if status == "accepted" else "trace_only"
    assert logged[0].before == {"review_status": "suggested", "metric_origin": "trace_only"}
    assert logged[0].after == {
        "review_status": status,
        "metric_origin": origin,
        "deleted": status == "rejected",
    }
    with factory() as db:
        chain = db.scalars(
            select(Event).where(Event.case_file_id == plan.case_file_id).order_by(Event.id)
        )
        chain_dicts = [{**event_content(event), "hash": event.hash} for event in chain]
    assert verify_chain(chain_dicts).valid


def foreign_id(
    kind: str, client: TestClient, factory: sessionmaker[Session], owner: User, plan: Plan
) -> str:
    if kind == "unknown":
        return str(uuid.uuid4())
    if kind == "other_plan_same_case":
        other = make_plan(client, factory, owner, case_id=str(plan.case_file_id))
        return add_suggestions(factory, other.current_revision_id, 1)[0]
    if kind == "other_case":
        other = make_plan(client, factory, owner)
        return add_suggestions(factory, other.current_revision_id, 1)[0]
    [entity_id] = add_suggestions(factory, plan.current_revision_id, 1)
    with factory() as db:
        row = db.get(Entity, uuid.UUID(entity_id))
        assert row is not None
        if kind == "deleted":
            row.deleted_at = datetime.now(UTC)
        else:  # manual
            row.source = {"method": "manual", "job_id": None, "parent_entity_id": None}
            row.review_status = "accepted"
        db.commit()
    return entity_id


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("unknown", "not_in_revision"),
        ("other_plan_same_case", "not_in_revision"),
        ("other_case", "not_in_revision"),
        ("deleted", "deleted"),
        ("manual", "not_a_suggestion"),
    ],
)
def test_one_invalid_id_changes_nothing_and_writes_no_event(
    client: TestClient,
    plan: Plan,
    owner: User,
    factory: sessionmaker[Session],
    kind: str,
    reason: str,
) -> None:
    """H5-4: all or nothing; 422 names the offending ids."""
    ids = add_suggestions(factory, plan.current_revision_id, 99)
    bad = foreign_id(kind, client, factory, owner, plan)
    before = {key: (row.review_status, row.deleted_at) for key, row in rows(factory).items()}

    response = review(client, plan.current_revision_id, [*ids[:50], bad, *ids[50:]], "accepted")

    assert response.status_code == 422, response.text
    body = response.json()
    assert (body["code"], body["invalid_ids"], body["reasons"]) == (
        "bulk_review_invalid",
        [bad],
        {bad: reason},
    )
    after = {key: (row.review_status, row.deleted_at) for key, row in rows(factory).items()}
    assert after == before
    assert events(factory, EventKind.ENTITY_REVIEWED) == []


def test_scaled_drawing_suggestion_is_promoted_only_when_accepted(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    """H5-10: ``trace_only`` while suggested, ``scaled_drawing`` after acceptance."""
    ids = add_suggestions(factory, plan.current_revision_id, 2)
    created = client.post(
        f"{REVISIONS_URL}/{plan.current_revision_id}/entities",
        json={
            "entities": [
                {
                    "type": "point",
                    "layer": "L_ACCESS",
                    "geometry": {"kind": "point", "coords_m": [[1.0, 1.0]]},
                }
            ]
        },
    )
    assert created.status_code == 201, created.text
    assert plan_status(factory, plan.id) == "approximate"
    assert {row.metric_origin for key, row in rows(factory).items() if key in ids} == {"trace_only"}

    response = review(client, plan.current_revision_id, ids, "accepted")

    assert response.status_code == 200, response.text
    assert response.json()["metric_status"] == "metric"
    assert {rows(factory)[key].metric_origin for key in ids} == {"scaled_drawing"}
    assert plan_status(factory, plan.id) == "metric"
    status = client.get(f"{API_V1_PREFIX}/plans/{plan.id}/metric-status").json()
    assert (status["status"], status["blocking_entities"]) == ("metric", [])


@pytest.mark.parametrize(
    ("mode", "as_metric"),
    [
        (SourceMode.SCHEMATIC_SKETCH, True),
        (SourceMode.SCHEMATIC_SKETCH, False),
        (SourceMode.SCALED_DRAWING, False),
    ],
)
def test_without_a_metric_scaled_drawing_an_accepted_suggestion_stays_trace_only(
    client: TestClient,
    owner: User,
    factory: sessionmaker[Session],
    mode: SourceMode,
    as_metric: bool,
) -> None:
    """H5-10: no promotion in ``schematic_sketch`` nor without ``as_metric``."""
    plan = make_plan(client, factory, owner, mode=mode, as_metric=as_metric)
    ids = add_suggestions(factory, plan.current_revision_id, 3)

    response = review(client, plan.current_revision_id, ids, "accepted")

    assert response.status_code == 200, response.text
    assert response.json()["metric_status"] == "approximate"
    assert {(row.review_status, row.metric_origin) for row in rows(factory).values()} == {
        ("accepted", "trace_only")
    }


def test_rejected_suggestions_leave_the_editor_metric_status_and_calibration_lock(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    kept, *dropped = add_suggestions(factory, plan.current_revision_id, 3)

    response = review(client, plan.current_revision_id, dropped, "rejected")

    assert response.status_code == 200, response.text
    listed = client.get(f"{REVISIONS_URL}/{plan.current_revision_id}/entities").json()
    assert [entity["id"] for entity in listed["entities"]] == [kept]
    stored = rows(factory)
    assert all(stored[key].deleted_at is not None for key in dropped)
    assert stored[dropped[0]].source["method"] == "cv" and stored[dropped[0]].confidence == 0.75
    with factory() as db:
        row = db.get(Plan, plan.id)
        assert row is not None and not locking_entities_exist(db, row)
    assert (
        review(client, plan.current_revision_id, [kept], "accepted").json()["metric_status"]
        == "metric"
    )


def test_reviewing_to_the_current_state_is_a_no_op_without_events(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    accepted, rejected, pending = add_suggestions(factory, plan.current_revision_id, 3)
    assert review(client, plan.current_revision_id, [accepted], "accepted").status_code == 200
    assert review(client, plan.current_revision_id, [rejected], "rejected").status_code == 200

    again = review(client, plan.current_revision_id, [accepted, pending], "accepted")
    rejected_again = review(client, plan.current_revision_id, [rejected], "rejected")

    assert (again.json()["changed_ids"], again.json()["unchanged_ids"]) == ([pending], [accepted])
    assert (rejected_again.json()["changed"], rejected_again.json()["unchanged"]) == (0, 1)
    assert [str(e.entity_id) for e in events(factory, EventKind.ENTITY_REVIEWED)] == [
        accepted,
        rejected,
        pending,
    ]


def test_accepted_cannot_be_rejected_and_rejected_cannot_be_accepted(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    accepted, rejected = add_suggestions(factory, plan.current_revision_id, 2)
    review(client, plan.current_revision_id, [accepted], "accepted")
    review(client, plan.current_revision_id, [rejected], "rejected")

    to_rejected = review(client, plan.current_revision_id, [accepted], "rejected")
    to_accepted = review(client, plan.current_revision_id, [rejected], "accepted")

    assert to_rejected.json()["reasons"] == {accepted: "invalid_transition"}
    assert to_accepted.json()["reasons"] == {rejected: "deleted"}
    assert len(events(factory, EventKind.ENTITY_REVIEWED)) == 2


@pytest.mark.parametrize(
    ("mode", "origin"),
    [(SourceMode.SCALED_DRAWING, "scaled_drawing"), (SourceMode.SCHEMATIC_SKETCH, "trace_only")],
)
@pytest.mark.parametrize(
    "body",
    [
        {
            "operation": "move_vertex",
            "geometry": {**POLYLINE, "coords_m": [[0, 0], [4, 0], [4, 3]]},
        },
        {"operation": "reclassify", "layer": "L_ACCESS"},
    ],
)
def test_editing_a_suggestion_marks_it_corrected_and_promotes_it(
    client: TestClient,
    owner: User,
    factory: sessionmaker[Session],
    mode: SourceMode,
    origin: str,
    body: dict[str, Any],
) -> None:
    """CA-05: an edited suggestion is ``corrected``; ``source`` and ``confidence`` stay."""
    plan = make_plan(client, factory, owner, mode=mode)
    [entity_id] = add_suggestions(factory, plan.current_revision_id, 1)

    response = client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)

    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["review_status"], out["metric_origin"]) == ("corrected", origin)
    assert (out["source"]["method"], out["confidence"]) == ("cv", 0.75)
    [event] = events(factory, EventKind.ENTITY_UPDATED)
    assert event.before is not None and event.after is not None
    assert (event.before["review_status"], event.after["review_status"]) == (
        "suggested",
        "corrected",
    )
    assert event.after["metric_origin"] == origin
    accepted = review(client, plan.current_revision_id, [entity_id], "accepted").json()
    assert (accepted["changed"], accepted["unchanged"]) == (0, 1)


def test_a_fixed_length_suggestion_is_corrected_and_keeps_its_declared_origin(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    """Promotion never downgrades a dimension-based origin (H3)."""
    [entity_id] = add_suggestions(factory, plan.current_revision_id, 1)
    for anchor in (0, 1):
        body = {"operation": "fix_length", "anchor_vertex": anchor, "value_m": 4.0}
        response = client.patch(f"{ENTITIES_URL}/{entity_id}", json=body)
        assert response.status_code == 200, response.text

    assert (response.json()["review_status"], response.json()["metric_origin"]) == (
        "corrected",
        "declared_dimension",
    )


def test_deleting_a_suggestion_rejects_it(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    [entity_id] = add_suggestions(factory, plan.current_revision_id, 1)

    response = client.delete(f"{ENTITIES_URL}/{entity_id}")

    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "rejected"
    [event] = events(factory, EventKind.ENTITY_DELETED)
    assert event.before is not None and event.before["review_status"] == "suggested"
    assert event.after == {"review_status": "rejected"}


def test_every_reviewed_suggestion_has_its_review_event(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    """H5-8: no non-manual entity is accepted or corrected without its event."""
    ids = add_suggestions(factory, plan.current_revision_id, 6)
    manual = {
        "type": "point",
        "layer": "L_ACCESS",
        "geometry": {"kind": "point", "coords_m": [[2.0, 2.0]]},
    }
    client.post(f"{REVISIONS_URL}/{plan.current_revision_id}/entities", json={"entities": [manual]})
    assert review(client, plan.current_revision_id, ids[:3], "accepted").status_code == 200
    assert review(client, plan.current_revision_id, ids[3:4], "rejected").status_code == 200
    edit = {"operation": "reclassify", "layer": "L_ACCESS"}
    assert client.patch(f"{ENTITIES_URL}/{ids[4]}", json=edit).status_code == 200

    reviewed = {
        (str(e.entity_id), e.after["review_status"])
        for e in events(factory, EventKind.ENTITY_REVIEWED, EventKind.ENTITY_UPDATED)
        if e.after is not None
        and e.after.get("review_status") in {"accepted", "corrected"}
        and (e.before or {}).get("review_status") == "suggested"
    }
    approved = {
        (key, row.review_status)
        for key, row in rows(factory).items()
        if row.source["method"] != "manual" and row.review_status in {"accepted", "corrected"}
    }
    assert approved == {(key, "accepted") for key in ids[:3]} | {(ids[4], "corrected")}
    assert approved <= reviewed


# Every write of ``review_status`` in the application, as (module, enclosing function).
# Keyword writes create rows: manual entities (accepted by definition, not suggestions),
# suggestions (suggested) and reopen copies (the review event belongs to the parent).
REVIEW_STATUS_WRITES = {
    ("entities/service.py", "create_entities"),
    ("entities/service.py", "_correct_suggestion"),
    ("entities/service.py", "delete_entity"),
    ("entities/bulk_review.py", "bulk_review"),
    ("revisions/service.py", "_copy"),
    ("worker/tasks_suggest.py", "_insert"),
}


def review_status_writes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC_DIR.rglob("*.py")):
        relative = path.relative_to(SRC_DIR).as_posix()
        if relative.startswith("db/models/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                targets = node.targets if isinstance(node, ast.Assign) else []
                flat = [e for t in targets for e in (t.elts if isinstance(t, ast.Tuple) else [t])]
                attrs = [t for t in flat if isinstance(t, ast.Attribute)]
                # Keyword writes: ``Entity(...)``, ``update(...).values(...)``; response
                # models (``...Out(...)``) only echo the status.
                response = isinstance(node, ast.Call) and ast.unparse(node.func).endswith("Out")
                keywords = node.keywords if isinstance(node, ast.Call) and not response else []
                if any(a.attr == "review_status" for a in attrs) or any(
                    k.arg == "review_status" for k in keywords
                ):
                    found.add((relative, function.name))
    return found


def function_source(relative: str, name: str) -> str:
    tree = ast.parse((SRC_DIR / relative).read_text(encoding="utf-8"))
    [function] = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    return ast.unparse(function)


def test_no_code_path_approves_a_suggestion_without_an_event() -> None:
    """H5-8 statically: each write of ``review_status`` is known and paired with its event."""
    assert review_status_writes() == REVIEW_STATUS_WRITES
    assert "ReviewStatus.ACCEPTED" not in function_source("entities/service.py", "delete_entity")
    assert "EventKind.ENTITY_REVIEWED" in function_source("entities/bulk_review.py", "bulk_review")
    update = function_source("entities/service.py", "update_entity")
    assert "_correct_suggestion(" in update and "EventKind.ENTITY_UPDATED" in update
    created = function_source("entities/service.py", "create_entities")
    assert "'method': 'manual'" in created
    assert "ReviewStatus.SUGGESTED" in function_source("worker/tasks_suggest.py", "_insert")


def test_reader_is_denied_with_an_audit_event_and_csrf_is_required(
    app: FastAPI, stack: ExitStack, client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    ids = add_suggestions(factory, plan.current_revision_id, 2)
    reader = insert_user(factory)
    with factory() as db:
        db.add(CaseMember(case_file_id=plan.case_file_id, user_id=reader.id, permissions=["read"]))
        db.commit()

    denied = review(signed_in(app, stack, reader), plan.current_revision_id, ids, "accepted")
    del client.headers[CSRF_HEADER]
    no_csrf = review(client, plan.current_revision_id, ids, "accepted")

    assert (denied.status_code, no_csrf.status_code) == (403, 403)
    [event] = events(factory, EventKind.ACCESS_DENIED)
    assert event.after is not None
    assert event.after["route"] == f"{REVISIONS_URL}/{{revision_id}}/entities:bulk-review"
    assert {row.review_status for row in rows(factory).values()} == {"suggested"}


def test_lease_validated_plan_frozen_revision_and_unknown_revision_are_rejected(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    ids = add_suggestions(factory, plan.current_revision_id, 1)
    revision_id = plan.current_revision_id

    set_plan(factory, plan.id, edit_lease=None)
    no_lease = review(client, revision_id, ids, "accepted")
    assert client.post(f"{API_V1_PREFIX}/plans/{plan.id}/lease").status_code == 200
    set_plan(factory, plan.id, status=PlanStatus.VALIDATED)
    validated = review(client, revision_id, ids, "accepted")
    set_plan(factory, plan.id, status=PlanStatus.EDITING)
    with factory() as db:
        revision = db.get(Revision, revision_id)
        assert revision is not None
        revision.state, revision.snapshot = "frozen", {}
        revision.snapshot_sha256, revision.frozen_at = "0" * 64, datetime.now(UTC)
        db.commit()
    frozen = review(client, revision_id, ids, "accepted")
    unknown = review(client, uuid.uuid4(), ids, "accepted")

    assert (no_lease.status_code, no_lease.json()["code"]) == (409, "lease_required")
    assert (validated.status_code, validated.json()["code"]) == (409, "V-10")
    assert frozen.status_code == 409 and "frozen" in frozen.json()["detail"]
    assert unknown.status_code == 404
    assert events(factory, EventKind.ENTITY_REVIEWED) == []


@pytest.mark.parametrize(
    "body",
    [
        {"ids": [], "review_status": "accepted"},
        {"ids": [str(uuid.uuid4()) for _ in range(2001)], "review_status": "accepted"},
        {"ids": [str(uuid.UUID(int=1))] * 2, "review_status": "accepted"},
        {"ids": [str(uuid.uuid4())], "review_status": "corrected"},
        {"ids": [str(uuid.uuid4())], "review_status": "accepted", "reason": "x"},
    ],
)
def test_invalid_bodies_are_422(client: TestClient, plan: Plan, body: dict[str, Any]) -> None:
    url = f"{REVISIONS_URL}/{plan.current_revision_id}/entities:bulk-review"
    response = client.post(url, json=body)

    assert response.status_code == 422, response.text
    assert "code" not in response.json()


def test_2000_ids_pass_validation(client: TestClient, plan: Plan) -> None:
    ids = [str(uuid.uuid4()) for _ in range(2000)]

    response = review(client, plan.current_revision_id, ids, "accepted")

    assert response.status_code == 422, response.text
    assert len(response.json()["invalid_ids"]) == 2000


def test_the_colon_route_is_documented_and_does_not_shadow_the_entity_list(
    app: FastAPI,
) -> None:
    paths = app.openapi()["paths"]
    assert list(paths[f"{REVISIONS_URL}/{{revision_id}}/entities:bulk-review"]) == ["post"]
    assert set(paths[f"{REVISIONS_URL}/{{revision_id}}/entities"]) == {"get", "post"}
