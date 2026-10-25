"""Calibration proposal and acceptance endpoints (REQ-03, US-03, V-05, V-08)."""

import math
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.calibration import V08_MESSAGE
from sketch2cad.db.base import Base
from sketch2cad.db.models import (
    Calibration,
    CaseMember,
    Entity,
    Event,
    Plan,
    PlanStatus,
    Source,
    User,
    UserRole,
)
from sketch2cad.geometry import apply_h, invert_h

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
PLANS_URL = f"{API_V1_PREFIX}/plans"
WIDTH_MM, HEIGHT_MM = 200.0, 100.0
PAPER_CORNERS = [(0.0, 0.0), (WIDTH_MM, 0.0), (WIDTH_MM, HEIGHT_MM), (0.0, HEIGHT_MM)]
# Oblique photo: paper mm -> image px, a proper perspective (not affine).
PAPER_TO_IMAGE = np.array([[9.0, 1.5, 300.0], [0.8, 8.0, 200.0], [0.0004, 0.0009, 1.0]])
IMAGE_CORNERS = [apply_h(PAPER_TO_IMAGE, corner) for corner in PAPER_CORNERS]
CENTER_PX = apply_h(PAPER_TO_IMAGE, (100.0, 50.0))


@pytest.fixture(scope="module")
def tables(db_engine: Any) -> Iterator[None]:
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


def make_plan(
    client: TestClient,
    factory: sessionmaker[Session],
    case_id: str,
    owner: User,
    dpi: int | None = None,
    source_mode: str = "schematic_sketch",
) -> str:
    with factory() as db:
        source = Source(
            case_file_id=uuid.UUID(case_id),
            original_filename="croquis.png",
            media_type="image/png",
            byte_size=1024,
            sha256=uuid.uuid4().hex * 2,
            storage_key=f"sources/{uuid.uuid4().hex}",
            dpi=None if dpi is None else Decimal(dpi),
            uploaded_by=owner.id,
        )
        db.add(source)
        db.commit()
        source_id = str(source.id)
    body = {"title": "Plano general", "primary_source_id": source_id, "source_mode": source_mode}
    response = client.post(f"{CASES_URL}/{case_id}/plans", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def plan_id(client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User) -> str:
    return make_plan(client, factory, case_id, owner)


def photo_body(**extra: Any) -> dict[str, Any]:
    return {
        "method": "photo_homography_4pt",
        "control_points": [{"image_px": list(p)} for p in IMAGE_CORNERS],
        "known_dimensions_mm": {"width": WIDTH_MM, "height": HEIGHT_MM},
        **extra,
    }


def check_point(offset_x_mm: float) -> dict[str, Any]:
    """The image centre declared ``offset_x_mm`` away from its true paper position."""
    return {"image_px": list(CENTER_PX), "paper_mm": [100.0 + offset_x_mm, 50.0]}


def propose(client: TestClient, plan_id: str, body: dict[str, Any]) -> Any:
    return client.put(f"{PLANS_URL}/{plan_id}/calibration", json=body)


def accept(client: TestClient, plan_id: str, body: dict[str, Any], as_metric: bool) -> Any:
    return client.post(
        f"{PLANS_URL}/{plan_id}/calibration/accept", json={**body, "as_metric": as_metric}
    )


def calibrations(factory: sessionmaker[Session], plan_id: str) -> list[Calibration]:
    with factory() as db:
        query = select(Calibration).where(Calibration.plan_id == uuid.UUID(plan_id))
        return list(db.scalars(query.order_by(Calibration.accepted_at, Calibration.id)))


def calibration_events(factory: sessionmaker[Session]) -> list[Event]:
    kinds = [EventKind.CALIBRATION_PROPOSED.value, EventKind.CALIBRATION_ACCEPTED.value]
    with factory() as db:
        return list(db.scalars(select(Event).where(Event.kind.in_(kinds)).order_by(Event.id)))


def load_plan(factory: sessionmaker[Session], plan_id: str) -> Plan:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        return plan


def add_entity(
    factory: sessionmaker[Session], plan_id: str, owner: User, deleted: bool = False
) -> None:
    plan = load_plan(factory, plan_id)
    with factory() as db:
        db.add(
            Entity(
                revision_id=plan.current_revision_id,
                type="line",
                layer="L_STRUCTURE",
                geometry={"kind": "line", "coords_m": [[0.0, 0.0], [1.0, 0.0]]},
                source={"method": "manual", "job_id": None, "parent_entity_id": None},
                review_status="accepted",
                metric_origin="none",
                created_by=owner.id,
                deleted_at=datetime.now(UTC) if deleted else None,
            )
        )
        db.commit()


def test_proposal_of_an_oblique_photo_maps_corners_and_persists_nothing(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    response = propose(client, plan_id, photo_body())

    assert response.status_code == 200, response.text
    body = response.json()
    matrix = np.array(body["image_to_paper"])
    assert matrix.shape == (3, 3)
    np.testing.assert_allclose(apply_h(matrix, IMAGE_CORNERS), PAPER_CORNERS, atol=1e-4)
    np.testing.assert_allclose(invert_h(matrix), PAPER_TO_IMAGE, rtol=1e-5, atol=1e-8)
    assert body["reprojection_error_basis"] == "control_points"
    assert body["reprojection_error_mm"]["max"] < 1e-4
    assert body["meets_tolerance"] is True
    assert body["max_reprojection_error_mm"] == 1.0
    assert [p["role"] for p in body["control_points"]] == ["control"] * 4
    assert [p["paper_mm"] for p in body["control_points"]] == [list(c) for c in PAPER_CORNERS]
    assert body["scale"] is None
    assert calibrations(factory, plan_id) == []
    assert calibration_events(factory) == []
    assert load_plan(factory, plan_id).status is PlanStatus.CALIBRATION_PENDING


def test_check_points_give_the_reprojection_error_and_do_not_change_the_fit(
    client: TestClient, plan_id: str
) -> None:
    plain = propose(client, plan_id, photo_body()).json()

    response = propose(client, plan_id, photo_body(check_points=[check_point(1.3)]))

    assert response.status_code == 200, response.text
    body = response.json()
    np.testing.assert_allclose(body["image_to_paper"], plain["image_to_paper"])
    assert body["reprojection_error_basis"] == "check_points"
    assert body["reprojection_error_mm"]["max"] == pytest.approx(1.3, abs=1e-4)
    assert body["reprojection_error_mm"]["rms"] == pytest.approx(1.3, abs=1e-4)
    assert body["meets_tolerance"] is False
    check = body["control_points"][-1]
    assert (check["role"], check["error_mm"]) == ("check", pytest.approx(1.3, abs=1e-4))


def test_as_metric_with_a_1_3_mm_error_is_422_v05_and_writes_nothing(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    response = accept(client, plan_id, photo_body(check_points=[check_point(1.3)]), True)

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert "supera el máximo" in problem["detail"]
    assert problem["detail"] == (
        "El error de calibración es de 1,30 mm y supera el máximo de 1,00 mm. "
        "Repite los puntos o continúa como aproximado."
    )
    assert problem["code"] == "V-05"
    assert problem["reprojection_error_mm"]["max"] == pytest.approx(1.3, abs=1e-4)
    assert problem["max_reprojection_error_mm"] == 1.0
    assert calibrations(factory, plan_id) == []
    assert calibration_events(factory) == []
    assert load_plan(factory, plan_id).status is PlanStatus.CALIBRATION_PENDING


def test_the_same_error_is_accepted_as_approximate(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    response = accept(client, plan_id, photo_body(check_points=[check_point(1.3)]), False)

    assert response.status_code == 200, response.text
    assert response.json()["metric_status"] == "approximate"
    assert response.json()["status"] == "EDITING"
    assert response.json()["calibration"]["as_metric"] is False


def test_tolerance_comes_from_the_environment(
    client: TestClient, plan_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CALIBRATION_MAX_REPROJECTION_ERROR_MM", "1.5")

    proposal = propose(client, plan_id, photo_body(check_points=[check_point(1.3)])).json()
    response = accept(client, plan_id, photo_body(check_points=[check_point(1.3)]), True)

    assert (proposal["meets_tolerance"], proposal["max_reprojection_error_mm"]) == (True, 1.5)
    assert response.status_code == 200, response.text


def test_accepted_calibration_stores_points_matrix_and_errors_and_emits_events(
    client: TestClient, plan_id: str, owner: User, factory: sessionmaker[Session]
) -> None:
    body = photo_body(check_points=[check_point(0.4)])
    proposal = propose(client, plan_id, body).json()

    response = accept(client, plan_id, body, True)

    assert response.status_code == 200, response.text
    plan_body = response.json()
    assert plan_body["status"] == "EDITING"
    assert plan_body["metric_status"] == "approximate"
    [stored] = calibrations(factory, plan_id)
    assert stored.is_active and stored.as_metric
    assert stored.method == "photo_homography_4pt"
    assert stored.accepted_by == owner.id
    assert stored.image_to_paper == proposal["image_to_paper"]
    assert np.array(stored.image_to_paper).shape == (3, 3)
    assert stored.control_points == proposal["control_points"]
    assert [p["role"] for p in stored.control_points] == ["control"] * 4 + ["check"]
    assert stored.reprojection_error_mm is not None
    assert set(stored.reprojection_error_mm) == {"max", "rms"}
    assert stored.reprojection_error_mm["max"] == pytest.approx(0.4, abs=1e-4)
    assert stored.reprojection_error_mm["rms"] == pytest.approx(0.4, abs=1e-4)
    assert plan_body["calibration"]["id"] == str(stored.id)
    assert plan_body["calibration"]["reprojection_error_mm"] == stored.reprojection_error_mm

    proposed, accepted = calibration_events(factory)
    assert [proposed.kind, accepted.kind] == ["calibration.proposed", "calibration.accepted"]
    plan = load_plan(factory, plan_id)
    for event in (proposed, accepted):
        assert event.plan_id == plan.id
        assert event.case_file_id == plan.case_file_id
        assert event.revision_id == plan.current_revision_id
        assert event.entity_id == stored.id
        assert (event.actor_id, event.actor_role) == (owner.id, "technician")
    assert proposed.after == proposal
    assert accepted.before == {"status": "CALIBRATION_PENDING", "metric_status": "non_metric"}
    assert accepted.after == {
        "calibration_id": str(stored.id),
        "previous_calibration_id": None,
        "method": "photo_homography_4pt",
        "as_metric": True,
        "status": "EDITING",
        "metric_status": "approximate",
    }


def test_metric_scaled_drawing_with_paper_scale_is_metric(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    plan_id = make_plan(client, factory, case_id, owner, source_mode="scaled_drawing")

    response = accept(client, plan_id, photo_body(paper_scale=100), True)

    assert response.status_code == 200, response.text
    assert response.json()["metric_status"] == "metric"
    assert response.json()["calibration"]["paper_scale"] == 100


def test_metric_scaled_drawing_without_paper_scale_is_422(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    plan_id = make_plan(client, factory, case_id, owner, source_mode="scaled_drawing")

    response = accept(client, plan_id, photo_body(), True)

    assert response.status_code == 422, response.text
    assert calibrations(factory, plan_id) == []


def test_paper_scale_on_a_schematic_sketch_is_422(client: TestClient, plan_id: str) -> None:
    response = propose(client, plan_id, photo_body(paper_scale=100))

    assert response.status_code == 422, response.text


def test_method_none_leaves_the_plan_non_metric(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    proposal = propose(client, plan_id, {"method": "none"}).json()
    response = accept(client, plan_id, {"method": "none"}, False)

    assert proposal["image_to_paper"] is None
    assert (proposal["reprojection_error_mm"], proposal["meets_tolerance"]) == (None, False)
    assert response.status_code == 200, response.text
    assert response.json()["metric_status"] == "non_metric"
    assert response.json()["status"] == "EDITING"
    [stored] = calibrations(factory, plan_id)
    assert (stored.method, stored.image_to_paper, stored.as_metric) == ("none", None, False)
    assert load_plan(factory, plan_id).metric_status.value == "non_metric"


def test_as_metric_with_method_none_is_422(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    response = accept(client, plan_id, {"method": "none"}, True)

    assert response.status_code == 422, response.text
    assert calibrations(factory, plan_id) == []
    assert load_plan(factory, plan_id).status is PlanStatus.CALIBRATION_PENDING


def test_put_calibration_with_one_entity_is_409_v08(
    client: TestClient, plan_id: str, owner: User, factory: sessionmaker[Session]
) -> None:
    add_entity(factory, plan_id, owner)

    response = propose(client, plan_id, photo_body())

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == V08_MESSAGE
    assert response.json()["detail"] == (
        "No puedes recalibrar un plano con entidades. Descártalas o crea otro plano."
    )
    assert response.json()["code"] == "V-08"


def test_accept_with_one_entity_is_409_and_keeps_the_calibration(
    client: TestClient, plan_id: str, owner: User, factory: sessionmaker[Session]
) -> None:
    assert accept(client, plan_id, photo_body(), True).status_code == 200
    add_entity(factory, plan_id, owner)

    response = accept(client, plan_id, {"method": "none"}, False)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == V08_MESSAGE
    [stored] = calibrations(factory, plan_id)
    assert stored.is_active and stored.method == "photo_homography_4pt"


def test_deleted_entities_do_not_lock_the_calibration(
    client: TestClient, plan_id: str, owner: User, factory: sessionmaker[Session]
) -> None:
    add_entity(factory, plan_id, owner, deleted=True)

    assert propose(client, plan_id, photo_body()).status_code == 200


def test_recalibrating_an_empty_plan_replaces_the_active_calibration(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    assert accept(client, plan_id, photo_body(), True).status_code == 200

    response = accept(client, plan_id, {"method": "none"}, False)

    assert response.status_code == 200, response.text
    first, second = calibrations(factory, plan_id)
    assert (first.is_active, second.is_active) == (False, True)
    assert response.json()["calibration"]["id"] == str(second.id)
    assert response.json()["metric_status"] == "non_metric"
    accepted = calibration_events(factory)[-1]
    assert accepted.before == {"status": "EDITING", "metric_status": "approximate"}
    assert accepted.after is not None
    assert accepted.after["previous_calibration_id"] == str(first.id)


def test_scan_dpi_uses_the_source_dpi(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    plan_id = make_plan(client, factory, case_id, owner, dpi=300)

    response = propose(client, plan_id, {"method": "scan_dpi"})

    assert response.status_code == 200, response.text
    body = response.json()
    k = 25.4 / 300
    np.testing.assert_allclose(body["image_to_paper"], [[k, 0, 0], [0, k, 0], [0, 0, 1]])
    assert body["scale"] == {
        "mm_per_px": pytest.approx(k),
        "dpi": 300.0,
        "dpi_discrepancy_pct": None,
        "within_tolerance": None,
    }
    assert (body["reprojection_error_mm"], body["meets_tolerance"]) == (None, True)


def test_scan_dpi_without_dpi_is_422(client: TestClient, plan_id: str) -> None:
    response = propose(client, plan_id, {"method": "scan_dpi"})

    assert response.status_code == 422, response.text
    assert "DPI" in response.json()["detail"]


def test_scan_segment_reports_the_difference_with_the_dpi(
    client: TestClient, factory: sessionmaker[Session], case_id: str, owner: User
) -> None:
    plan_id = make_plan(client, factory, case_id, owner, dpi=254)
    # 1000 px at 254 dpi is 100 mm; the drawn ruler says 102 mm (+2 %).
    body = {
        "method": "scan_segment",
        "control_points": [{"image_px": [100, 200]}, {"image_px": [1100, 200]}],
        "segment_mm": 102.0,
    }

    response = propose(client, plan_id, body)

    assert response.status_code == 200, response.text
    scale = response.json()["scale"]
    assert scale["mm_per_px"] == pytest.approx(0.102)
    assert scale["dpi_discrepancy_pct"] == pytest.approx(2.0)
    assert scale["within_tolerance"] is False
    points = response.json()["control_points"]
    assert math.dist(points[0]["paper_mm"], points[1]["paper_mm"]) == pytest.approx(102.0)


@pytest.mark.parametrize(
    "body",
    [
        photo_body(control_points=[{"image_px": list(p)} for p in IMAGE_CORNERS[:3]]),
        photo_body(known_dimensions_mm=None),
        photo_body(control_points=[{"image_px": [0, 0]}, {"image_px": [1, 1]}] * 2),
        photo_body(check_points=[{"image_px": [10, 10]}]),
        {"method": "scan_segment", "control_points": [{"image_px": [0, 0]}], "segment_mm": 10},
        {"method": "scan_segment", "control_points": [{"image_px": [5, 5]}] * 2, "segment_mm": 10},
        {"method": "none", "control_points": [{"image_px": [0, 0]}]},
        {"method": "photo"},
        photo_body(segment_mm=-1),
    ],
    ids=[
        "three-points",
        "no-paper-positions",
        "degenerate-quad",
        "check-without-paper",
        "segment-one-point",
        "segment-coincident",
        "none-with-points",
        "unknown-method",
        "negative-segment",
    ],
)
def test_invalid_inputs_are_422(client: TestClient, plan_id: str, body: dict[str, Any]) -> None:
    response = propose(client, plan_id, body)

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")


def test_plan_past_editing_cannot_be_calibrated(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        plan.status = PlanStatus.REVIEW_PENDING
        db.commit()

    assert propose(client, plan_id, photo_body()).status_code == 409
    assert accept(client, plan_id, photo_body(), False).status_code == 409


def test_mutations_without_csrf_token_are_403(
    client: TestClient, plan_id: str, factory: sessionmaker[Session]
) -> None:
    del client.headers[CSRF_HEADER]

    assert propose(client, plan_id, photo_body()).status_code == 403
    assert accept(client, plan_id, photo_body(), False).status_code == 403
    assert calibrations(factory, plan_id) == []


def test_reader_gets_403_with_access_denied(
    app: FastAPI, stack: ExitStack, case_id: str, plan_id: str, factory: sessionmaker[Session]
) -> None:
    reader = insert_user(factory)
    with factory() as db:
        db.add(CaseMember(case_file_id=uuid.UUID(case_id), user_id=reader.id, permissions=["read"]))
        db.commit()
    reader_client = signed_in(app, stack, reader)

    response = accept(reader_client, plan_id, photo_body(), False)

    assert response.status_code == 403
    assert calibrations(factory, plan_id) == []
    with factory() as db:
        denied = list(db.scalars(select(Event).where(Event.kind == "access.denied")))
    assert [(e.actor_id, e.after) for e in denied] == [
        (
            reader.id,
            {
                "method": "POST",
                "route": f"{PLANS_URL}/{{plan_id}}/calibration/accept",
                "permission": "edit",
                "status": 403,
            },
        )
    ]


def test_unknown_plan_is_404(client: TestClient, factory: sessionmaker[Session]) -> None:
    assert propose(client, str(uuid.uuid4()), photo_body()).status_code == 404
