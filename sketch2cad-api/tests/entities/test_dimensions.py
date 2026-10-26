"""Declared dimensions with the ``discrepancy`` flag (REQ-06, H3-7, spec sections 8.2, 11.4)."""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
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
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.config import Settings
from sketch2cad.db.base import Base
from sketch2cad.db.models import Entity, Plan, PlanStatus, Source, User, UserRole
from sketch2cad.entities.dimensions import (
    DimensionTolerances,
    dimension_discrepancy,
    discrepant_dimensions,
    has_discrepancy,
    measured_length,
)
from sketch2cad.entities.schemas import DimensionGeometry

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
CASES_URL = f"{API_V1_PREFIX}/case-files"
REVISIONS_URL = f"{API_V1_PREFIX}/revisions"
ENTITIES_URL = f"{API_V1_PREFIX}/entities"
TOLERANCE = 0.02


def dimension(p2_x: float, declared: float | None = 4.20, **extra: Any) -> dict[str, Any]:
    """A horizontal dimension from the origin to ``(p2_x, 0)``."""
    return {
        "kind": "dimension",
        "p1_m": [0.0, 0.0],
        "p2_m": [p2_x, 0.0],
        "offset_m": 0.5,
        "declared_value_m": declared,
        **extra,
    }


# Pure functions.


def test_measured_length_is_the_distance_between_the_points() -> None:
    assert measured_length((1.0, 1.0), (4.0, 5.0)) == 5.0
    assert measured_length([0.0, 0.0], [0.0, 0.0]) == 0.0


@pytest.mark.parametrize(
    ("measured", "expected"),
    [(4.24, True), (4.215, False), (4.22, False), (4.18, False), (4.17, True), (4.20, False)],
)
def test_discrepancy_is_a_difference_strictly_greater_than_the_tolerance(
    measured: float, expected: bool
) -> None:
    assert has_discrepancy(4.20, measured, TOLERANCE) is expected


@pytest.mark.parametrize(("declared", "measured"), [(1.0, 1.02), (4.0, 4.02), (10.0, 9.98)])
def test_a_difference_of_exactly_the_tolerance_is_not_a_discrepancy(
    declared: float, measured: float
) -> None:
    assert has_discrepancy(declared, measured, TOLERANCE) is False


def test_a_plain_measurement_never_has_a_discrepancy() -> None:
    assert has_discrepancy(None, 100.0, TOLERANCE) is False
    assert dimension_discrepancy("dimension", dimension(9.0, None), DimensionTolerances()) is False


def test_other_entity_types_have_no_discrepancy_value() -> None:
    line = {"kind": "line", "coords_m": [[0, 0], [1, 0]]}
    assert dimension_discrepancy("line", line, DimensionTolerances()) is None


def test_the_check_recomputes_measured_and_changes_neither_value() -> None:
    stale = dimension(4.24, measured_value_m=4.20)
    snapshot = {**stale}

    assert dimension_discrepancy("dimension", stale, DimensionTolerances()) is True
    assert stale == snapshot


def test_tolerances_default_to_the_spec_and_follow_settings_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DimensionTolerances().discrepancy_tolerance_m == 0.02
    monkeypatch.delenv("DIMENSION_DISCREPANCY_TOLERANCE_M", raising=False)
    assert DimensionTolerances.from_environment() == DimensionTolerances()
    monkeypatch.setenv("DIMENSION_DISCREPANCY_TOLERANCE_M", "0.05")
    assert DimensionTolerances.from_environment().discrepancy_tolerance_m == 0.05
    settings = Settings.model_construct(DIMENSION_DISCREPANCY_TOLERANCE_M=0.001)
    loose = DimensionTolerances.from_settings(settings)
    assert dimension_discrepancy("dimension", dimension(4.215), loose) is True
    assert dimension_discrepancy("dimension", dimension(4.24), DimensionTolerances(0.05)) is False


@pytest.mark.parametrize("value", ["0", "-0.01", "abc"])
def test_an_invalid_tolerance_in_the_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DIMENSION_DISCREPANCY_TOLERANCE_M", value)
    with pytest.raises(ValidationError):
        DimensionTolerances.from_environment()


def test_a_non_positive_tolerance_is_rejected() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        DimensionTolerances(0.0)


def test_schema_replaces_a_client_measured_value_and_keeps_the_declared_one() -> None:
    geometry = DimensionGeometry.model_validate(dimension(4.24, measured_value_m=1.0))

    assert geometry.measured_value_m == 4.24
    assert geometry.declared_value_m == 4.20


def test_discrepant_dimensions_lists_live_flagged_dimensions_in_order() -> None:
    def row(kind: str, geometry: dict[str, Any], deleted: bool = False) -> Entity:
        return Entity(
            id=uuid.uuid4(),
            type=kind,
            geometry=geometry,
            deleted_at=datetime.now(UTC) if deleted else None,
        )

    flagged_a = row("dimension", dimension(4.24))
    flagged_b = row("dimension", dimension(5.0))
    entities = [
        flagged_a,
        row("dimension", dimension(4.215)),
        row("dimension", dimension(9.0, None)),
        row("dimension", dimension(4.30), deleted=True),
        row("line", {"kind": "line", "coords_m": [[0, 0], [1, 0]]}),
        flagged_b,
    ]

    assert discrepant_dimensions(entities, DimensionTolerances()) == [flagged_a, flagged_b]


# API.


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
    monkeypatch.delenv("DIMENSION_DISCREPANCY_TOLERANCE_M", raising=False)
    return create_app()


@pytest.fixture
def client(app: FastAPI, factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
        )
        db.add(user)
        db.commit()
        username = user.username
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(app, base_url="https://testserver"))
        response = client.post(
            f"{API_V1_PREFIX}/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
        yield client


@pytest.fixture
def revision_id(client: TestClient, factory: sessionmaker[Session]) -> str:
    """Draft revision 1 of a plan in ``EDITING`` whose edit lease the client holds."""
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
        user = db.scalars(select(User)).one()
        source = Source(
            case_file_id=uuid.UUID(case_id),
            original_filename="croquis.png",
            media_type="image/png",
            byte_size=1024,
            sha256=uuid.uuid4().hex * 2,
            storage_key=f"sources/{uuid.uuid4().hex}",
            uploaded_by=user.id,
        )
        db.add(source)
        db.commit()
        source_id = str(source.id)
    body = {"title": "Plano general", "primary_source_id": source_id}
    response = client.post(f"{CASES_URL}/{case_id}/plans", json=body)
    assert response.status_code == 201, response.text
    plan_id = uuid.UUID(response.json()["id"])
    with factory() as db:
        plan = db.get(Plan, plan_id)
        assert plan is not None
        plan.status = PlanStatus.EDITING
        db.commit()
        current = str(plan.current_revision_id)
    response = client.post(f"{API_V1_PREFIX}/plans/{plan_id}/lease")
    assert response.status_code == 200, response.text
    return current


def create(client: TestClient, revision_id: str, *geometries: dict[str, Any]) -> list[Any]:
    items = [{"type": g["kind"], "layer": "L_DIMENSIONS", "geometry": g} for g in geometries]
    response = client.post(f"{REVISIONS_URL}/{revision_id}/entities", json={"entities": items})
    assert response.status_code == 201, response.text
    return response.json()["entities"]


def listed(client: TestClient, revision_id: str) -> dict[str, Any]:
    response = client.get(f"{REVISIONS_URL}/{revision_id}/entities")
    assert response.status_code == 200, response.text
    return {entity["id"]: entity for entity in response.json()["entities"]}


def stored_geometry(factory: sessionmaker[Session], entity_id: str) -> dict[str, Any]:
    with factory() as db:
        entity = db.get(Entity, uuid.UUID(entity_id))
        assert entity is not None
        return dict(entity.geometry)


def test_h3_7_declared_4_20_flags_4_24_and_not_4_215_and_changes_no_value(
    client: TestClient, revision_id: str, factory: sessionmaker[Session]
) -> None:
    far, near = create(client, revision_id, dimension(4.24), dimension(4.215))

    assert (far["discrepancy"], near["discrepancy"]) == (True, False)
    before = {e["id"]: stored_geometry(factory, e["id"]) for e in (far, near)}
    entities = listed(client, revision_id)
    for entity_id, measured in ((far["id"], 4.24), (near["id"], 4.215)):
        assert stored_geometry(factory, entity_id) == before[entity_id]
        assert before[entity_id]["declared_value_m"] == 4.20
        assert before[entity_id]["measured_value_m"] == measured
        assert entities[entity_id]["geometry"] == before[entity_id]
    assert entities[far["id"]]["discrepancy"] is True
    assert entities[near["id"]]["discrepancy"] is False


def test_a_stale_client_measured_value_is_overwritten_and_declared_kept(
    client: TestClient, revision_id: str, factory: sessionmaker[Session]
) -> None:
    (created,) = create(client, revision_id, dimension(4.24, measured_value_m=4.20))

    stored = stored_geometry(factory, created["id"])
    assert (stored["declared_value_m"], stored["measured_value_m"]) == (4.20, 4.24)
    assert created["discrepancy"] is True


def test_moving_p2_recomputes_measured_and_discrepancy(
    client: TestClient, revision_id: str, factory: sessionmaker[Session]
) -> None:
    (created,) = create(client, revision_id, dimension(4.24))
    url = f"{ENTITIES_URL}/{created['id']}"

    response = client.patch(
        url, json={"operation": "move_vertex", "geometry": dimension(4.21, measured_value_m=4.24)}
    )

    assert response.status_code == 200, response.text
    assert response.json()["discrepancy"] is False
    stored = stored_geometry(factory, created["id"])
    assert (stored["declared_value_m"], stored["measured_value_m"]) == (4.20, 4.21)
    response = client.patch(url, json={"operation": "move_vertex", "geometry": dimension(4.30)})
    assert response.json()["discrepancy"] is True
    assert stored_geometry(factory, created["id"])["measured_value_m"] == 4.30
    assert listed(client, revision_id)[created["id"]]["discrepancy"] is True


def test_plain_measurements_and_other_types_carry_false_and_null(
    client: TestClient, revision_id: str
) -> None:
    (plain,) = create(client, revision_id, dimension(9.0, None))
    line = {"kind": "line", "coords_m": [[0, 0], [1, 0]]}
    (other,) = create(client, revision_id, line)

    assert plain["discrepancy"] is False
    assert other["discrepancy"] is None
    assert plain["metric_origin"] == "none"


def test_the_tolerance_is_read_on_every_request(
    client: TestClient, revision_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    (created,) = create(client, revision_id, dimension(4.24))
    assert created["discrepancy"] is True

    monkeypatch.setenv("DIMENSION_DISCREPANCY_TOLERANCE_M", "0.05")

    assert listed(client, revision_id)[created["id"]]["discrepancy"] is False
    response = client.delete(f"{ENTITIES_URL}/{created['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["discrepancy"] is False
