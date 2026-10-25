"""Local frame: rigid transform of draft entities and its events (REQ-04, US-05, H2-5, H2-6)."""

import itertools
import math
import random
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
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
    User,
    UserRole,
)
from sketch2cad.geometry.frame import (
    DEFAULT_FRAME,
    LocalFrame,
    RigidTransform,
    normalize_deg,
    rigid_between,
    world_frame,
)
from sketch2cad.plans.local_frame import PROVISIONAL_PAPER_SCALE, transform_geometry

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
REVISIONS_URL = f"{API_V1_PREFIX}/revisions"
TOLERANCE_M = 1e-9
FRAME_KINDS = (EventKind.LOCAL_FRAME_SET, EventKind.LOCAL_FRAME_CHANGED)
FIRST = {"origin_paper_mm": [12.5, 30.0], "x_axis_angle_deg": 0.0, "north_angle_deg": 0.0}
MOVED = {"origin_paper_mm": [80.25, 140.5], "x_axis_angle_deg": 33.7, "north_angle_deg": 15.0}

finite = st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False)
angles = st.floats(min_value=-720.0, max_value=720.0, allow_nan=False, allow_infinity=False)
points = st.tuples(finite, finite)


def _frame(origin: tuple[float, float], angle: float) -> LocalFrame:
    return LocalFrame.create(origin, angle, 0.0)


frames = st.builds(_frame, points, angles)


def _world(frame: LocalFrame, local: tuple[float, float], scale: float) -> tuple[float, float]:
    """Independent local -> world map: ``o + R(theta) p`` with ``o`` paper->world (Y down)."""
    ox, oy = frame.origin_paper_mm[0] * scale / 1000, -frame.origin_paper_mm[1] * scale / 1000
    t = math.radians(frame.x_axis_angle_deg)
    x, y = local
    return (ox + math.cos(t) * x - math.sin(t) * y, oy + math.sin(t) * x + math.cos(t) * y)


# --- Pure geometry -------------------------------------------------------------------------


@given(frames, frames, st.lists(points, min_size=1, max_size=8), st.sampled_from([50, 100, 500]))
def test_transform_between_frames_keeps_world_positions(
    old: LocalFrame, new: LocalFrame, locals_m: list[tuple[float, float]], scale: int
) -> None:
    transform = rigid_between(world_frame(old, scale), world_frame(new, scale))

    for point, moved in zip(locals_m, transform.apply_points(locals_m), strict=True):
        assert math.dist(_world(old, point, scale), _world(new, moved, scale)) <= 1e-9


@given(points, st.floats(-180, 180), st.lists(points, min_size=2, max_size=8), angles)
def test_rigid_transform_keeps_distances_and_rotates_angles(
    shift: tuple[float, float],
    theta: float,
    pts: list[tuple[float, float]],
    angle: float,
) -> None:
    transform = RigidTransform(shift[0], shift[1], theta)
    moved = transform.apply_points(pts)

    for (a, b), (c, d) in zip(
        itertools.combinations(pts, 2), itertools.combinations(moved, 2), strict=True
    ):
        assert abs(math.dist(a, b) - math.dist(c, d)) <= TOLERANCE_M
    # A direction at ``angle`` maps to the direction at ``apply_angle(angle)``.
    head = (pts[0][0] + math.cos(math.radians(angle)), pts[0][1] + math.sin(math.radians(angle)))
    tip = transform.apply_point(head)
    turned = math.radians(transform.apply_angle(angle))
    assert math.isclose(tip[0] - moved[0][0], math.cos(turned), abs_tol=1e-9)
    assert math.isclose(tip[1] - moved[0][1], math.sin(turned), abs_tol=1e-9)


def test_quarter_turns_are_exact_and_angles_are_normalized() -> None:
    transform = RigidTransform(1.0, 2.0, 90.0)

    assert transform.apply_point((3.0, 4.0)) == (-3.0, 5.0)
    assert transform.apply_angle(300.0) == 30.0
    assert RigidTransform(5.0, 0.0, 0.0).apply_angle(-30.0) == -30.0
    assert [normalize_deg(a) for a in (-90.0, 360.0, 725.5, -1e-20)] == [270.0, 0.0, 5.5, 0.0]


def test_same_frame_is_the_identity_and_default_frame_is_paper_origin() -> None:
    frame = LocalFrame.create((10.0, 20.0), 45.0, 0.0)
    placed = world_frame(frame, 100)

    assert rigid_between(placed, placed) == RigidTransform(0.0, 0.0, 0.0)
    assert world_frame(DEFAULT_FRAME, 100).origin_m == (0.0, 0.0)
    assert placed.origin_m == (1.0, -2.0)


@pytest.mark.parametrize("origin", [(math.nan, 0.0), (0.0, math.inf), (1.0,)])
def test_local_frame_rejects_non_finite_or_malformed_origins(origin: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="origin_paper_mm"):
        LocalFrame.create(origin, 0.0, 0.0)


def test_transform_geometry_moves_every_metric_field() -> None:
    transform = RigidTransform(1.0, -1.0, 90.0)
    dimension = {"kind": "dimension", "p1_m": [0.0, 0.0], "p2_m": [3.0, 4.0], "offset_m": 0.5}
    symbol: dict[str, Any] = {
        "kind": "symbol",
        "anchor_m": [2.0, 0.0],
        "rotation_deg": 300.0,
        "scale": 2.0,
        "symbol_id": "door",
        "attributes": {},
    }

    moved_dimension = transform_geometry(
        {**dimension, "declared_value_m": None, "measured_value_m": 5.0}, transform
    )
    moved_symbol = transform_geometry(symbol, transform)

    assert moved_dimension == {
        **dimension,
        "p1_m": [1.0, -1.0],
        "p2_m": [-3.0, 2.0],
        "declared_value_m": None,
        "measured_value_m": 5.0,
    }
    assert moved_symbol == {**symbol, "anchor_m": [1.0, 1.0], "rotation_deg": 30.0}


# --- API -----------------------------------------------------------------------------------


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


@pytest.fixture
def plan(client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User) -> Plan:
    """A plan in ``EDITING`` with draft revision 1 and no calibration."""
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
        row = db.get(Plan, plan_id)
        assert row is not None
        return row


@pytest.fixture
def calibrated(plan: Plan, owner: User, factory: sessionmaker[Session]) -> Plan:
    """``plan`` with an active ``scan_dpi`` calibration whose local frame is not set."""
    with factory() as db:
        db.add(Calibration(plan_id=plan.id, method="scan_dpi", accepted_by=owner.id))
        db.commit()
    return plan


def put_frame(client: TestClient, plan: Plan, body: dict[str, Any]) -> Any:
    return client.put(f"{PLANS_URL}/{plan.id}/local-frame", json=body)


def post_entities(client: TestClient, revision_id: Any, items: list[dict[str, Any]]) -> Any:
    response = client.post(f"{REVISIONS_URL}/{revision_id}/entities", json={"entities": items})
    assert response.status_code == 201, response.text
    return response.json()["entities"]


def frame_events(factory: sessionmaker[Session]) -> list[Event]:
    with factory() as db:
        query = select(Event).where(Event.kind.in_([kind.value for kind in FRAME_KINDS]))
        return list(db.scalars(query.order_by(Event.id)))


def stored_geometry(factory: sessionmaker[Session]) -> dict[uuid.UUID, str]:
    """The stored JSONB text of every entity geometry, by id."""
    with factory() as db:
        rows = db.execute(text("SELECT id, geometry::text FROM entity"))
        return {row[0]: row[1] for row in rows}


def geometries(factory: sessionmaker[Session]) -> dict[uuid.UUID, dict[str, Any]]:
    with factory() as db:
        return {e.id: e.geometry for e in db.scalars(select(Entity))}


def _xy(rng: random.Random) -> list[float]:
    return [rng.uniform(-50.0, 50.0), rng.uniform(-50.0, 50.0)]


def random_entities(count: int, seed: int = 53) -> list[dict[str, Any]]:
    """Deterministic mix of every geometry kind, including rotated texts and symbols."""
    rng = random.Random(seed)  # noqa: S311 - deterministic test data
    makers: list[Callable[[], tuple[str, dict[str, Any]]]] = [
        lambda: ("L_STRUCTURE", {"kind": "line", "coords_m": [_xy(rng), _xy(rng)]}),
        lambda: (
            "L_STRUCTURE",
            {"kind": "polyline", "coords_m": [_xy(rng), _xy(rng), _xy(rng)], "closed": True},
        ),
        lambda: ("L_EVIDENCE", {"kind": "point", "coords_m": [_xy(rng)]}),
        lambda: (
            "L_TEXT",
            {
                "kind": "text",
                "anchor_m": _xy(rng),
                "rotation_deg": rng.uniform(-180.0, 360.0),
                "height_mm_paper": 2.5,
                "content": "Calzada",
            },
        ),
        lambda: (
            "L_DIMENSIONS",
            {
                "kind": "dimension",
                "p1_m": _xy(rng),
                "p2_m": _xy(rng),
                "offset_m": rng.uniform(-1.0, 1.0),
                "declared_value_m": 4.2,
            },
        ),
        lambda: (
            "L_SYMBOLS",
            {
                "kind": "symbol",
                "anchor_m": _xy(rng),
                "rotation_deg": rng.uniform(0.0, 360.0),
                "scale": 1.5,
                "symbol_id": "generic_vehicle",
                "attributes": {},
            },
        ),
    ]
    items: list[dict[str, Any]] = []
    for index in range(count):
        layer, geometry = makers[index % len(makers)]()
        items.append({"type": geometry["kind"], "layer": layer, "geometry": geometry})
    return items


def metric_points(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    found = [tuple(p) for p in geometry.get("coords_m", [])]
    found += [tuple(geometry[k]) for k in ("anchor_m", "p1_m", "p2_m") if k in geometry]
    return [(float(x), float(y)) for x, y in found]


def test_first_frame_writes_local_frame_set_and_returns_it(
    client: TestClient, calibrated: Plan, factory: sessionmaker[Session]
) -> None:
    response = put_frame(client, calibrated, {**FIRST, "x_axis_angle_deg": -90.0})

    assert response.status_code == 200, response.text
    assert response.json()["calibration"]["local_frame"] == {
        "origin_paper_mm": [12.5, 30.0],
        "x_axis_angle_deg": 270.0,
        "north_angle_deg": 0.0,
    }
    [event] = frame_events(factory)
    assert event.kind == EventKind.LOCAL_FRAME_SET.value
    assert event.before is None
    assert event.after is not None
    assert event.after["local_frame"]["x_axis_angle_deg"] == 270.0
    assert event.after["paper_scale"] == PROVISIONAL_PAPER_SCALE


def test_first_frame_moves_entities_drawn_in_the_default_frame(
    client: TestClient, calibrated: Plan, factory: sessionmaker[Session]
) -> None:
    line = {"kind": "line", "coords_m": [[1.25, -3.0], [4.0, -3.0]]}
    post_entities(
        client,
        calibrated.current_revision_id,
        [{"type": "line", "layer": "L_STRUCTURE", "geometry": line}],
    )

    assert put_frame(client, calibrated, FIRST).status_code == 200

    [geometry] = geometries(factory).values()
    # Origin (12.5 mm, 30 mm) on paper is (1.25 m, -3 m) in the world at 1:100.
    assert geometry["coords_m"] == [[0.0, 0.0], [2.75, 0.0]]
    [event] = frame_events(factory)
    assert event.kind == EventKind.LOCAL_FRAME_SET.value
    assert event.after is not None
    assert event.after["transform"] == {"dx_m": -1.25, "dy_m": 3.0, "theta_deg": 0.0}
    assert event.after["transformed_entities"] == 1


def test_changing_origin_and_axis_moves_50_entities_rigidly_with_one_event(
    client: TestClient, calibrated: Plan, factory: sessionmaker[Session]
) -> None:
    assert put_frame(client, calibrated, FIRST).status_code == 200
    created = post_entities(client, calibrated.current_revision_id, random_entities(50))
    before = geometries(factory)

    response = put_frame(client, calibrated, MOVED)

    assert response.status_code == 200, response.text
    after = geometries(factory)
    assert len(created) == len(after) == 50
    old_points = [p for e in sorted(before) for p in metric_points(before[e])]
    new_points = [p for e in sorted(after) for p in metric_points(after[e])]
    old_pairs = itertools.combinations(old_points, 2)
    new_pairs = itertools.combinations(new_points, 2)
    gaps = [
        abs(math.dist(a, b) - math.dist(c, d))
        for (a, b), (c, d) in zip(old_pairs, new_pairs, strict=True)
    ]
    assert max(gaps) <= TOLERANCE_M
    old_frame, new_frame = LocalFrame.from_json(FIRST), LocalFrame.from_json(MOVED)
    for old_p, new_p in zip(old_points, new_points, strict=True):
        world_old = _world(old_frame, old_p, PROVISIONAL_PAPER_SCALE)
        assert math.dist(world_old, _world(new_frame, new_p, PROVISIONAL_PAPER_SCALE)) <= 1e-9
    for entity_id, geometry in after.items():
        old = before[entity_id]
        if "rotation_deg" in old:
            expected = normalize_deg(old["rotation_deg"] - 33.7)
            assert math.isclose(geometry["rotation_deg"], expected, abs_tol=1e-9)
        if old["kind"] == "dimension":
            assert geometry["offset_m"] == old["offset_m"]
            assert geometry["declared_value_m"] == old["declared_value_m"]
            assert abs(geometry["measured_value_m"] - old["measured_value_m"]) <= TOLERANCE_M
    events = frame_events(factory)
    changed = [e for e in events if e.kind == EventKind.LOCAL_FRAME_CHANGED.value]
    assert len(changed) == 1
    assert changed[0].before == {"local_frame": FIRST}
    assert changed[0].after is not None
    assert changed[0].after["local_frame"] == MOVED
    assert changed[0].after["transform"]["theta_deg"] == pytest.approx(-33.7)
    assert changed[0].after["transformed_entities"] == 50


def test_changing_only_north_leaves_every_coordinate_byte_identical(
    client: TestClient, calibrated: Plan, factory: sessionmaker[Session]
) -> None:
    assert put_frame(client, calibrated, MOVED).status_code == 200
    post_entities(client, calibrated.current_revision_id, random_entities(12))
    stored = stored_geometry(factory)
    with factory() as db:
        stamps = {e.id: e.updated_at for e in db.scalars(select(Entity))}

    response = put_frame(client, calibrated, {**MOVED, "north_angle_deg": 200.0})

    assert response.status_code == 200, response.text
    assert response.json()["calibration"]["local_frame"]["north_angle_deg"] == 200.0
    assert stored_geometry(factory) == stored
    with factory() as db:
        assert {e.id: e.updated_at for e in db.scalars(select(Entity))} == stamps
    changed = frame_events(factory)[-1]
    assert changed.kind == EventKind.LOCAL_FRAME_CHANGED.value
    assert changed.after is not None
    assert changed.after["transform"] is None
    assert changed.after["transformed_entities"] == 0


def test_an_unchanged_frame_writes_nothing(
    client: TestClient, calibrated: Plan, factory: sessionmaker[Session]
) -> None:
    assert put_frame(client, calibrated, FIRST).status_code == 200

    response = put_frame(client, calibrated, {**FIRST, "x_axis_angle_deg": 360.0})

    assert response.status_code == 200
    assert [e.kind for e in frame_events(factory)] == [EventKind.LOCAL_FRAME_SET.value]


def test_frozen_revisions_and_deleted_entities_are_untouched(
    client: TestClient, calibrated: Plan, owner: User, factory: sessionmaker[Session]
) -> None:
    [frozen] = post_entities(client, calibrated.current_revision_id, random_entities(1))
    with factory() as db:
        old = db.get(Revision, calibrated.current_revision_id)
        assert old is not None
        old.state, old.snapshot, old.snapshot_sha256 = "frozen", {}, "0" * 64
        old.frozen_at = datetime.now(UTC)
        draft = Revision(plan_id=calibrated.id, number=2, state="draft", created_by=owner.id)
        db.add(draft)
        db.flush()
        db.get_one(Plan, calibrated.id).current_revision_id = draft.id
        db.commit()
        draft_id = draft.id
    live, deleted = post_entities(client, draft_id, random_entities(2, seed=7))
    assert client.delete(f"{API_V1_PREFIX}/entities/{deleted['id']}").status_code == 200
    stored = stored_geometry(factory)

    assert put_frame(client, calibrated, MOVED).status_code == 200

    now = stored_geometry(factory)
    unchanged = {uuid.UUID(frozen["id"]), uuid.UUID(deleted["id"])}
    assert {i for i in stored if now[i] == stored[i]} == unchanged
    assert now[uuid.UUID(live["id"])] != stored[uuid.UUID(live["id"])]


def test_a_plan_without_an_active_calibration_is_409(
    client: TestClient, plan: Plan, factory: sessionmaker[Session]
) -> None:
    response = put_frame(client, plan, FIRST)

    assert response.status_code == 409, response.text
    assert "calibration" in response.json()["detail"]
    assert frame_events(factory) == []


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (PlanStatus.VALIDATED, "V-10"),
        (PlanStatus.REVIEW_PENDING, None),
        (PlanStatus.CALIBRATION_PENDING, None),
    ],
)
def test_plans_that_cannot_be_edited_reject_the_frame_with_409(
    client: TestClient,
    calibrated: Plan,
    factory: sessionmaker[Session],
    status: PlanStatus,
    code: str | None,
) -> None:
    set_plan(factory, calibrated.id, status=status)

    response = put_frame(client, calibrated, FIRST)

    assert response.status_code == 409, response.text
    assert response.json().get("code") == code
    with factory() as db:
        assert db.scalars(select(Calibration.local_frame)).one() is None
    assert frame_events(factory) == []


@pytest.mark.parametrize(
    "body",
    [
        {**FIRST, "origin_paper_mm": [1.0, 2.0, 3.0]},
        {**FIRST, "x_axis_angle_deg": "north"},
        {"origin_paper_mm": [1.0, 2.0], "x_axis_angle_deg": 0.0},
        {**FIRST, "extra": 1},
    ],
)
def test_invalid_bodies_are_422(
    client: TestClient, calibrated: Plan, factory: sessionmaker[Session], body: dict[str, Any]
) -> None:
    assert put_frame(client, calibrated, body).status_code == 422
    assert frame_events(factory) == []


def test_readers_strangers_and_missing_csrf_are_rejected(
    app: FastAPI,
    stack: ExitStack,
    client: TestClient,
    case_id: str,
    calibrated: Plan,
    factory: sessionmaker[Session],
) -> None:
    reader = insert_user(factory)
    with factory() as db:
        db.add(CaseMember(case_file_id=uuid.UUID(case_id), user_id=reader.id, permissions=["read"]))
        db.commit()
    stranger = signed_in(app, stack, insert_user(factory))

    assert put_frame(signed_in(app, stack, reader), calibrated, FIRST).status_code == 403
    assert put_frame(stranger, calibrated, FIRST).status_code == 403
    assert client.put(f"{PLANS_URL}/{uuid.uuid4()}/local-frame", json=FIRST).status_code == 404
    del client.headers[CSRF_HEADER]
    assert put_frame(client, calibrated, FIRST).status_code == 403
    assert frame_events(factory) == []
