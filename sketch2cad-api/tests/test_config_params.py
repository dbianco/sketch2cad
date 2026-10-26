"""Layered configuration: registry, resolution order, admin API and consumers (REQ-19)."""

import math
import re
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind
from sketch2cad.audit.canonical import event_content
from sketch2cad.audit.verify import verify_chain
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.config_params import (
    PARAMETERS,
    ConfigValueError,
    delete_override,
    get_param,
    get_params,
    set_override,
)
from sketch2cad.db.base import Base
from sketch2cad.db.models import (
    Calibration,
    ConfigOverride,
    ConfigScope,
    Entity,
    Event,
    IncidentType,
    Plan,
    Source,
    User,
    UserRole,
)
from sketch2cad.geometry import apply_h
from sketch2cad.worker.app import apply_schema, to_conninfo

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "sketch2cad"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
PARAMS_URL = f"{API_V1_PREFIX}/config/params"
TOLERANCE = "dimension.discrepancy_tolerance_m"
REPROJECTION = "calibration.max_reprojection_error_mm"
TRAFFIC = IncidentType.TRAFFIC_COLLISION
SPEC_DEFAULTS: dict[str, Any] = {
    "qc.min_short_side_amber": 1500,
    "qc.min_short_side_red": 900,
    "qc.min_laplacian_var_amber": 100.0,
    "qc.min_laplacian_var_red": 40.0,
    "qc.min_contrast_amber": 60.0,
    "calibration.max_reprojection_error_mm": 1.0,
    "calibration.max_scale_discrepancy_pct": 1.0,
    "dimension.discrepancy_tolerance_m": 0.02,
    "export.control_distance_tolerance_pct": 0.5,
    "suggestions.min_confidence_highlight": 0.7,
    "suggest_lines.threshold_block_size": 31,
    "suggest_lines.threshold_c": 10.0,
    "suggest_lines.opening_kernel_px": 3,
    "suggest_lines.simplify_tolerance_mm": 0.8,
    "suggest_lines.min_length_mm": 5.0,
    "suggest_lines.merge_angle_deg": 5.0,
    "ocr.min_confidence": 0.6,
    "ai_assist.enabled": True,
    "legal.disclaimer_enabled": True,
}
# Criterion: rg -n '0\.02|1\.0\b' src/sketch2cad/geometry src/sketch2cad/calibration
LITERAL = re.compile(r"0\.02|1\.0\b")


# Registry and default layer (no database).


def test_registry_defaults_are_the_spec_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for spec in PARAMETERS.values():
        monkeypatch.delenv(spec.env_var, raising=False)
    assert {name: spec.code_default for name, spec in PARAMETERS.items()} == SPEC_DEFAULTS
    assert get_params(PARAMETERS) == SPEC_DEFAULTS


def test_the_environment_variable_still_defines_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIMENSION_DISCREPANCY_TOLERANCE_M", "0.05")
    assert get_param(TOLERANCE, incident_type=TRAFFIC) == 0.05
    monkeypatch.setenv("DIMENSION_DISCREPANCY_TOLERANCE_M", "0")
    with pytest.raises(ValidationError):
        get_param(TOLERANCE)


def test_an_unknown_parameter_raises_a_key_error_naming_it() -> None:
    with pytest.raises(KeyError, match="dimension.unknown"):
        get_param("dimension.unknown")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (TOLERANCE, 0),
        (TOLERANCE, -0.01),
        (TOLERANCE, "0.05"),
        (TOLERANCE, True),
        (TOLERANCE, math.nan),
        ("qc.min_short_side_amber", 1200.5),
        ("suggest_lines.threshold_block_size", 32),
        ("legal.disclaimer_enabled", 1),
    ],
)
def test_values_that_do_not_fit_the_registry_are_rejected(name: str, value: object) -> None:
    with pytest.raises(ConfigValueError, match=name):
        PARAMETERS[name].validate(value)


def test_no_hardcoded_tolerance_literal_in_geometry_or_calibration() -> None:
    """Criterion: ``rg -n '0\\.02|1\\.0\\b' src/sketch2cad/geometry src/sketch2cad/calibration
    | rg -v 'config_params|test'`` prints nothing."""
    hits = [
        f"{path.relative_to(SRC_DIR)}:{number}: {line.strip()}"
        for folder in ("geometry", "calibration")
        for path in sorted((SRC_DIR / folder).rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if LITERAL.search(line) and not re.search("config_params|test", f"{path}:{line}")
    ]
    assert hits == []


# Database fixtures.


@pytest.fixture(scope="module")
def tables(db_engine: Any, database_url: str, test_schema: str) -> Iterator[None]:
    """Application tables plus the Procrastinate schema (calibration accept defers a job)."""
    db_engine.dispose()
    Base.metadata.create_all(db_engine)
    apply_schema(to_conninfo(database_url, options=f"-csearch_path={test_schema}"))
    try:
        yield
    finally:
        Base.metadata.drop_all(db_engine)
        with db_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{test_schema}" CASCADE'))
            connection.execute(text(f'CREATE SCHEMA "{test_schema}"'))
        db_engine.dispose()


@pytest.fixture
def factory(
    tables: None, db_session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    for spec in PARAMETERS.values():
        monkeypatch.delenv(spec.env_var, raising=False)
    yield db_session_factory
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with db_session_factory() as db:
        db.execute(text(f"TRUNCATE {names}, procrastinate_jobs CASCADE"))
        db.commit()


def insert_user(factory: sessionmaker[Session], role: UserRole) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password(PASSWORD),
            role=role,
        )
        db.add(user)
        db.commit()
        return user


@pytest.fixture
def admin(factory: sessionmaker[Session]) -> User:
    return insert_user(factory, UserRole.ADMIN)


@pytest.fixture
def technician(factory: sessionmaker[Session]) -> User:
    return insert_user(factory, UserRole.TECHNICIAN)


def global_chain(factory: sessionmaker[Session]) -> list[dict[str, Any]]:
    with factory() as db:
        rows = db.scalars(select(Event).where(Event.case_file_id.is_(None)).order_by(Event.id))
        return [{**event_content(event), "hash": event.hash} for event in rows]


def kinds(factory: sessionmaker[Session]) -> list[str]:
    return [event["kind"] for event in global_chain(factory)]


# Resolution order and the write service.


def test_incident_type_override_beats_global_override_beats_default(
    factory: sessionmaker[Session], admin: User
) -> None:
    with factory() as db:
        assert get_param(TOLERANCE, incident_type=TRAFFIC, session=db) == 0.02
        set_override(
            db,
            name=TOLERANCE,
            scope=ConfigScope.INCIDENT_TYPE,
            incident_type=TRAFFIC,
            value=0.05,
            actor=admin,
        )
        db.commit()
        assert get_param(TOLERANCE, incident_type=TRAFFIC, session=db) == 0.05
        assert get_param(TOLERANCE, incident_type=IncidentType.CRIME_SCENE, session=db) == 0.02
        assert get_param(TOLERANCE, session=db) == 0.02
        assert get_param(TOLERANCE, incident_type=TRAFFIC) == 0.02  # no session: default only

        set_override(
            db, name=TOLERANCE, scope=ConfigScope.GLOBAL, incident_type=None, value=3, actor=admin
        )
        db.commit()
        assert get_param(TOLERANCE, incident_type=TRAFFIC, session=db) == 0.05
        assert get_param(TOLERANCE, incident_type=IncidentType.CRIME_SCENE, session=db) == 3.0

        assert delete_override(
            db, name=TOLERANCE, scope=ConfigScope.INCIDENT_TYPE, incident_type=TRAFFIC, actor=admin
        )
        db.commit()
        assert get_param(TOLERANCE, incident_type=TRAFFIC, session=db) == 3.0
    assert kinds(factory) == [EventKind.CONFIG_CHANGED.value] * 3


def test_set_replaces_in_place_and_records_before_and_after(
    factory: sessionmaker[Session], admin: User
) -> None:
    with factory() as db:
        for value in (0.05, 0.04):
            set_override(
                db,
                name=TOLERANCE,
                scope=ConfigScope.INCIDENT_TYPE,
                incident_type=TRAFFIC,
                value=value,
                actor=admin,
            )
            db.commit()
        assert len(list(db.scalars(select(ConfigOverride)))) == 1
    first, second = global_chain(factory)
    target = {"key": TOLERANCE, "scope": "incident_type", "incident_type": "traffic_collision"}
    assert (first["before"], first["after"]) == (None, {**target, "value": 0.05})
    assert (second["before"], second["after"]) == (
        {**target, "value": 0.05},
        {**target, "value": 0.04},
    )
    assert second["actor_id"] == str(admin.id) and second["actor_role"] == "admin"


def test_one_row_per_key_scope_and_incident_type(
    factory: sessionmaker[Session], admin: User
) -> None:
    rows = [
        (ConfigScope.GLOBAL.value, None),
        (ConfigScope.INCIDENT_TYPE.value, TRAFFIC),
        (ConfigScope.INCIDENT_TYPE.value, IncidentType.OTHER),
    ]
    with factory() as db:
        for scope, incident_type in rows:
            db.add(
                ConfigOverride(
                    key=TOLERANCE,
                    scope=scope,
                    incident_type=incident_type,
                    value=0.05,
                    updated_by=admin.id,
                )
            )
        db.commit()
    for scope, incident_type in [*rows[:2], (ConfigScope.GLOBAL.value, TRAFFIC)]:
        with factory() as db, pytest.raises(IntegrityError):
            db.add(
                ConfigOverride(
                    key=TOLERANCE,
                    scope=scope,
                    incident_type=incident_type,
                    value=0.05,
                    updated_by=admin.id,
                )
            )
            db.commit()


# Admin API.


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


def signed_in(app: FastAPI, stack: ExitStack, user: User) -> TestClient:
    client = stack.enter_context(TestClient(app, base_url="https://testserver"))
    response = client.post(
        f"{API_V1_PREFIX}/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def admin_client(app: FastAPI, stack: ExitStack, admin: User) -> TestClient:
    return signed_in(app, stack, admin)


@pytest.fixture
def tech_client(app: FastAPI, stack: ExitStack, technician: User) -> TestClient:
    return signed_in(app, stack, technician)


def put(client: TestClient, name: str, value: object, incident_type: str | None = None) -> Any:
    scope = "global" if incident_type is None else "incident_type"
    body = {"value": value, "scope": scope, "incident_type": incident_type}
    return client.put(f"{PARAMS_URL}/{name}", json=body)


def config_events(factory: sessionmaker[Session]) -> list[str]:
    return [k for k in kinds(factory) if k == EventKind.CONFIG_CHANGED.value]


def test_admin_sets_and_removes_overrides_with_one_event_each(
    admin_client: TestClient, tech_client: TestClient, factory: sessionmaker[Session]
) -> None:
    response = put(admin_client, TOLERANCE, 0.05, "traffic_collision")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["type"], body["default"]) == (TOLERANCE, "float", 0.02)
    assert body["constraints"] == {"gt": 0.0}
    assert body["scopes"] == ["global", "incident_type"]
    assert [(o["scope"], o["incident_type"], o["value"]) for o in body["overrides"]] == [
        ("incident_type", "traffic_collision", 0.05)
    ]
    assert len(config_events(factory)) == 1

    listed = tech_client.get(PARAMS_URL)
    assert listed.status_code == 200
    by_name = {p["name"]: p for p in listed.json()["params"]}
    assert set(by_name) == set(SPEC_DEFAULTS)
    assert by_name[TOLERANCE]["overrides"][0]["value"] == 0.05
    assert by_name["ai_assist.enabled"]["scopes"] == []

    url = f"{PARAMS_URL}/{TOLERANCE}"
    params = {"scope": "incident_type", "incident_type": "traffic_collision"}
    response = admin_client.delete(url, params=params)
    assert response.status_code == 200, response.text
    assert response.json()["overrides"] == []
    assert len(config_events(factory)) == 2
    assert admin_client.delete(url, params=params).status_code == 404
    assert len(config_events(factory)) == 2

    chain = global_chain(factory)
    assert verify_chain(chain).valid
    assert verify_chain(chain).checked == len(chain)


def test_a_technician_cannot_write_and_the_denial_is_audited(
    tech_client: TestClient, factory: sessionmaker[Session]
) -> None:
    response = put(tech_client, TOLERANCE, 0.05)
    assert response.status_code == 403
    response = tech_client.delete(f"{PARAMS_URL}/{TOLERANCE}", params={"scope": "global"})
    assert response.status_code == 403

    with factory() as db:
        assert list(db.scalars(select(ConfigOverride))) == []
    events = [e for e in global_chain(factory) if e["kind"] == EventKind.ACCESS_DENIED.value]
    assert [e["after"]["permission"] for e in events] == ["role:admin", "role:admin"]
    assert config_events(factory) == []
    assert verify_chain(global_chain(factory)).valid


def test_a_write_without_csrf_token_is_rejected(
    admin_client: TestClient, factory: sessionmaker[Session]
) -> None:
    del admin_client.headers[CSRF_HEADER]
    assert put(admin_client, TOLERANCE, 0.05).status_code == 403
    assert config_events(factory) == []


@pytest.mark.parametrize(
    ("name", "body"),
    [
        (TOLERANCE, {"value": 0, "scope": "global"}),
        (TOLERANCE, {"value": -1.5, "scope": "global"}),
        (TOLERANCE, {"value": "0.05", "scope": "global"}),
        (TOLERANCE, {"value": True, "scope": "global"}),
        (TOLERANCE, {"value": [0.05], "scope": "global"}),
        ("dimension.unknown", {"value": 0.05, "scope": "global"}),
        (TOLERANCE, {"value": 0.05, "scope": "incident_type", "incident_type": "flood"}),
        (TOLERANCE, {"value": 0.05, "scope": "incident_type"}),
        (TOLERANCE, {"value": 0.05, "scope": "global", "incident_type": "traffic_collision"}),
        (TOLERANCE, {"value": 0.05, "scope": "case_file"}),
        ("ai_assist.enabled", {"value": False, "scope": "global"}),
        (
            "legal.disclaimer_enabled",
            {"value": False, "scope": "incident_type", "incident_type": "other"},
        ),
        ("qc.min_short_side_amber", {"value": 1200.5, "scope": "global"}),
    ],
)
def test_invalid_overrides_are_rejected_with_422(
    admin_client: TestClient, factory: sessionmaker[Session], name: str, body: dict[str, Any]
) -> None:
    response = admin_client.put(f"{PARAMS_URL}/{name}", json=body)
    assert response.status_code == 422, response.text
    with factory() as db:
        assert list(db.scalars(select(ConfigOverride))) == []
    assert config_events(factory) == []


# Consumers resolve with the incident type of the case file.

WIDTH_MM, HEIGHT_MM = 200.0, 100.0
PAPER_TO_IMAGE = np.array([[9.0, 1.5, 300.0], [0.8, 8.0, 200.0], [0.0004, 0.0009, 1.0]])
CORNERS_MM = [(0.0, 0.0), (WIDTH_MM, 0.0), (WIDTH_MM, HEIGHT_MM), (0.0, HEIGHT_MM)]


def make_plan(client: TestClient, factory: sessionmaker[Session], user: User) -> str:
    case = {
        "incident_type": "traffic_collision",
        "incident_date": "2026-10-20",
        "description": "Colisión entre dos vehículos",
        "declared_location": "Av. Libertador 1200",
    }
    response = client.post(f"{API_V1_PREFIX}/case-files", json=case)
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
            dpi=Decimal(300),
            uploaded_by=user.id,
        )
        db.add(source)
        db.commit()
        source_id = str(source.id)
    body = {"title": "Plano", "primary_source_id": source_id, "source_mode": "schematic_sketch"}
    response = client.post(f"{API_V1_PREFIX}/case-files/{case_id}/plans", json=body)
    assert response.status_code == 201, response.text
    plan_id = response.json()["id"]
    assert client.post(f"{API_V1_PREFIX}/plans/{plan_id}/lease").status_code == 200
    return plan_id


def accept_with_error(client: TestClient, plan_id: str, error_mm: float) -> Any:
    """Accept a 4-point photo calibration as metric whose check point is ``error_mm`` off."""
    centre = apply_h(PAPER_TO_IMAGE, (100.0, 50.0))
    body = {
        "method": "photo_homography_4pt",
        "control_points": [{"image_px": list(apply_h(PAPER_TO_IMAGE, c))} for c in CORNERS_MM],
        "known_dimensions_mm": {"width": WIDTH_MM, "height": HEIGHT_MM},
        "check_points": [{"image_px": list(centre), "paper_mm": [100.0 + error_mm, 50.0]}],
        "as_metric": True,
    }
    return client.post(f"{API_V1_PREFIX}/plans/{plan_id}/calibration/accept", json=body)


def test_calibration_accept_uses_the_incident_type_tolerance(
    tech_client: TestClient,
    admin_client: TestClient,
    factory: sessionmaker[Session],
    technician: User,
) -> None:
    plan_id = make_plan(tech_client, factory, technician)

    rejected = accept_with_error(tech_client, plan_id, 1.3)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "V-05"
    assert rejected.json()["max_reprojection_error_mm"] == 1.0

    assert put(admin_client, REPROJECTION, 2.0, "crime_scene").status_code == 200
    assert accept_with_error(tech_client, plan_id, 1.3).status_code == 422

    assert put(admin_client, REPROJECTION, 2.0, "traffic_collision").status_code == 200
    accepted = accept_with_error(tech_client, plan_id, 1.3)
    assert accepted.status_code == 200, accepted.text
    with factory() as db:
        query = select(Calibration.as_metric).where(Calibration.plan_id == uuid.UUID(plan_id))
        assert list(db.scalars(query)) == [True]


def test_entity_discrepancy_uses_the_incident_type_tolerance(
    tech_client: TestClient,
    admin_client: TestClient,
    factory: sessionmaker[Session],
    technician: User,
) -> None:
    plan_id = make_plan(tech_client, factory, technician)
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        revision_id = plan.current_revision_id
        db.add(
            Entity(
                revision_id=revision_id,
                type="dimension",
                layer="L_DIMENSIONS",
                geometry={
                    "kind": "dimension",
                    "p1_m": [0.0, 0.0],
                    "p2_m": [4.24, 0.0],
                    "offset_m": 0.5,
                    "declared_value_m": 4.20,
                },
                source={"method": "manual", "job_id": None, "parent_entity_id": None},
                review_status="accepted",
                metric_origin="none",
                created_by=technician.id,
            )
        )
        db.commit()
    url = f"{API_V1_PREFIX}/revisions/{revision_id}/entities"

    assert [e["discrepancy"] for e in tech_client.get(url).json()["entities"]] == [True]
    assert put(admin_client, TOLERANCE, 0.05, "traffic_collision").status_code == 200
    assert [e["discrepancy"] for e in tech_client.get(url).json()["entities"]] == [False]
    status = tech_client.get(f"{API_V1_PREFIX}/plans/{plan_id}/metric-status").json()
    assert status["discrepant_dimensions"] == []
