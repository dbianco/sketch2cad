"""Two-user authorization matrix over every OpenAPI operation with a path parameter (CA-12).

The operations come from the committed ``openapi.json``, so a new route is covered as soon
as the schema is regenerated. An owner creates a case file with a source, plan, revision,
entity, evidence, job and artifact through the API; an outsider (an authenticated user with
no membership) calls every operation with those real ids and must get 403 plus one
``access.denied`` event (H1-5, H6-3: non-members get 403, not 404). The owner reaches the
same ids, so the matrix cannot pass on wrong ids. A path parameter without an id builder,
an operation without path parameters missing from :data:`EXEMPT` and a stale exemption all
fail the suite instead of being skipped.
"""

import hashlib
import io
import json
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any

import cv2
import psycopg
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Engine, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.deps import CSRF_COOKIE, require_plan_permission
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.audit import EventKind
from sketch2cad.auth.csrf import CSRF_HEADER
from sketch2cad.auth.passwords import hash_password
from sketch2cad.config_params import AI_ASSIST_PARAM
from sketch2cad.db import session as db_session
from sketch2cad.db.base import Base
from sketch2cad.db.models import (
    Artifact,
    Calibration,
    Event,
    JobMeta,
    MetricStatus,
    Plan,
    PlanStatus,
    User,
    UserRole,
)
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.qc import synthetic
from sketch2cad.storage import FilesystemStorage
from sketch2cad.worker.app import apply_schema, to_conninfo

API = API_V1_PREFIX
OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi.json"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
HTTP_METHODS = ("get", "put", "post", "patch", "delete")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PATH_PARAM = re.compile(r"{([^}]+)}")
CSRF_DETAIL = "A valid CSRF token is required."

EXEMPT: dict[str, str] = {
    "POST /api/v1/auth/login": "Public by design: it is how a session is obtained.",
    "POST /api/v1/auth/logout": "Acts only on the caller's own session; no resource id.",
    "GET /api/v1/auth/me": "Returns only the caller's own user; no resource id.",
    "GET /api/v1/case-files": "Lists only the case files visible to the caller.",
    "POST /api/v1/case-files": "Creates a case file owned by the caller; no existing resource.",
    "GET /api/v1/catalog/active": "Global symbol catalog readable by every signed-in user.",
    "GET /api/v1/config/features": "Global feature switches readable by every signed-in user.",
    "GET /api/v1/config/params": "Global parameters readable by every signed-in user.",
    "GET /healthz": "Public liveness probe; no data.",
}
"""Operations outside the matrix, with the reason. Every one must exist in the schema."""

NOT_FOUND_BY_DESIGN: dict[str, str] = {}
"""Operations whose outsider answer is 404 without an event, with the reason (none today)."""

GLOBAL_PARAMS = frozenset({"name"})
"""Parameters of admin-only routes: the denial goes to the global chain (no case file)."""


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    params: tuple[str, ...]
    body_required: bool

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def mutating(self) -> bool:
        return self.method not in SAFE_METHODS

    @property
    def global_scope(self) -> bool:
        return any(param in GLOBAL_PARAMS for param in self.params)


@dataclass(frozen=True)
class World:
    """Real ids of the owner's resources, all in one case file."""

    case_id: str
    source_id: str
    plan_id: str
    revision_id: str
    entity_id: str
    evidence_id: str
    job_id: str
    artifact_id: str


@dataclass(frozen=True)
class Actors:
    owner: TestClient
    admin: TestClient
    outsider: TestClient
    outsider_id: uuid.UUID


ID_BUILDERS: dict[str, Callable[[World], str]] = {
    "case_id": lambda world: world.case_id,
    "source_id": lambda world: world.source_id,
    "plan_id": lambda world: world.plan_id,
    "revision_id": lambda world: world.revision_id,
    "entity_id": lambda world: world.entity_id,
    "evidence_id": lambda world: world.evidence_id,
    "job_id": lambda world: world.job_id,
    "artifact_id": lambda world: world.artifact_id,
    "name": lambda _world: AI_ASSIST_PARAM,
}
"""Path parameter name -> the real id of the owner's resource it takes."""


def operations(schema: dict[str, Any]) -> list[Operation]:
    """Every operation of an OpenAPI schema, with the parameters of its path template."""
    found: list[Operation] = []
    for path, item in schema["paths"].items():
        for method in HTTP_METHODS:
            if method not in item:
                continue
            body = item[method].get("requestBody", {})
            found.append(
                Operation(
                    method.upper(),
                    path,
                    tuple(PATH_PARAM.findall(path)),
                    bool(body.get("required", False)),
                )
            )
    return found


def committed_operations() -> list[Operation]:
    return operations(json.loads(OPENAPI_PATH.read_text(encoding="utf-8")))


def stale_exemptions(ops: list[Operation], exempt: dict[str, str]) -> list[str]:
    keys = {op.key for op in ops}
    return sorted(key for key in exempt if key not in keys)


def matrix_operations(ops: list[Operation], exempt: dict[str, str]) -> list[Operation]:
    return [op for op in ops if op.key not in exempt]


def missing_builders(op: Operation) -> list[str]:
    return [param for param in op.params if param not in ID_BUILDERS]


def url_for(op: Operation, world: World) -> str:
    missing = missing_builders(op)
    assert not missing, f"{op.key}: no id builder for {missing}; add one to ID_BUILDERS"
    return PATH_PARAM.sub(lambda match: ID_BUILDERS[match.group(1)](world), op.path)


def denied_events(factory: sessionmaker[Session], actor_id: uuid.UUID) -> list[Event]:
    with factory() as db:
        query = select(Event).where(
            Event.kind == EventKind.ACCESS_DENIED.value, Event.actor_id == actor_id
        )
        return list(db.scalars(query.order_by(Event.id)))


def check_outsider(
    op: Operation, actors: Actors, world: World, factory: sessionmaker[Session]
) -> list[str]:
    """Failures of ``op`` called by the outsider on the owner's ids (empty when denied)."""
    if not op.params:
        return [f"{op.key}: has no path parameter; add it to EXEMPT with a reason"]
    missing = missing_builders(op)
    if missing:
        return [f"{op.key}: no id builder for {missing}; add one to ID_BUILDERS"]
    before = len(denied_events(factory, actors.outsider_id))
    response = actors.outsider.request(op.method, url_for(op, world))
    new = denied_events(factory, actors.outsider_id)[before:]
    status = response.status_code
    if status == 404 and op.key in NOT_FOUND_BY_DESIGN:
        return [f"{op.key}: 404 by design must not record access.denied"] if new else []
    if status != 403:
        return [f"{op.key}: outsider got {status}, expected 403 + access.denied"]
    if len(new) != 1:
        return [f"{op.key}: expected one access.denied event, found {len(new)}"]
    event = new[0]
    expected_case = None if op.global_scope else uuid.UUID(world.case_id)
    recorded = event.after or {}
    wanted = {"method": op.method, "route": op.path, "status": 403}
    if event.case_file_id != expected_case or any(
        recorded.get(field) != value for field, value in wanted.items()
    ):
        return [f"{op.key}: access.denied does not describe the request: {recorded}"]
    return []


def check_permitted(op: Operation, actors: Actors, world: World) -> tuple[list[str], bool]:
    """Failures of the owner (admin for global routes) on the same ids, and if it was checked.

    GETs must succeed. A mutation with a required body is sent without one: 422 proves the
    id resolved and the permission check passed (bodies are validated after dependencies).
    Mutations without a required body are not replayed (they would change the data); their
    ids are proven by another operation taking the same parameter.
    """
    client = actors.admin if op.global_scope else actors.owner
    if op.mutating and not op.body_required:
        return [], False
    response = client.request(op.method, url_for(op, world))
    expected = 200 if not op.mutating else 422
    if response.status_code != expected:
        return [f"{op.key}: permitted user got {response.status_code}, expected {expected}"], True
    return [], True


@pytest.fixture(scope="module")
def matrix_engine(database_url: str) -> Iterator[Engine]:
    """Own schema with the app tables and the Procrastinate schema; dropped afterwards."""
    schema = f"test_authz_{uuid.uuid4().hex}"
    admin = to_conninfo(database_url)
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    engine = create_db_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        apply_schema(to_conninfo(database_url, options=f"-csearch_path={schema}"))
        yield engine
    finally:
        engine.dispose()
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture
def factory(
    matrix_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    session_factory = create_session_factory(matrix_engine)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: session_factory)
    yield session_factory
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with session_factory() as db:
        db.execute(sql_text(f"TRUNCATE {names}, procrastinate_jobs CASCADE"))
        db.commit()


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]) -> FastAPI:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    return create_app()


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as exit_stack:
        yield exit_stack


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


def signed_in(app: FastAPI, stack: ExitStack, user: User, *, csrf: bool = True) -> TestClient:
    client = stack.enter_context(TestClient(app, base_url="https://testserver"))
    response = client.post(
        f"{API}/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    if csrf:
        client.headers[CSRF_HEADER] = response.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def owner(factory: sessionmaker[Session]) -> User:
    return insert_user(factory, UserRole.TECHNICIAN)


@pytest.fixture
def actors(app: FastAPI, stack: ExitStack, factory: sessionmaker[Session], owner: User) -> Actors:
    outsider = insert_user(factory, UserRole.TECHNICIAN)
    return Actors(
        owner=signed_in(app, stack, owner),
        admin=signed_in(app, stack, insert_user(factory, UserRole.ADMIN)),
        outsider=signed_in(app, stack, outsider),
        outsider_id=outsider.id,
    )


def created_id(response: Any) -> str:
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def world(actors: Actors, factory: sessionmaker[Session], owner: User, tmp_path: Path) -> World:
    """The owner's case file with one resource of every kind a path parameter names."""
    client = actors.owner
    case_id = created_id(
        client.post(
            f"{API}/case-files",
            json={
                "incident_type": "traffic_collision",
                "incident_date": "2026-10-20",
                "description": "Colisión entre dos vehículos",
                "declared_location": "Av. Libertador 1200",
            },
        )
    )
    ok, buffer = cv2.imencode(".png", synthetic.sketch(600))
    assert ok
    data = buffer.tobytes()
    source_id = created_id(
        client.post(
            f"{API}/case-files/{case_id}/sources",
            files={"file": ("croquis.png", data, "application/octet-stream")},
            data={"client_sha256": hashlib.sha256(data).hexdigest()},
        )
    )
    plan_id = created_id(
        client.post(
            f"{API}/case-files/{case_id}/plans",
            json={"title": "Plano", "primary_source_id": source_id},
        )
    )
    with factory() as db:
        plan = db.get(Plan, uuid.UUID(plan_id))
        assert plan is not None
        plan.status = PlanStatus.EDITING
        plan.metric_status = MetricStatus.APPROXIMATE
        db.add(Calibration(plan_id=plan.id, method="scan_dpi", accepted_by=owner.id))
        db.commit()
    assert client.post(f"{API}/plans/{plan_id}/lease").status_code == 200
    revision_id = client.get(f"{API}/plans/{plan_id}").json()["current_revision_id"]
    geometry = {"kind": "polyline", "coords_m": [[0.0, 0.0], [4.2, 0.0]], "closed": False}
    response = client.post(
        f"{API}/revisions/{revision_id}/entities",
        json={"entities": [{"type": "polyline", "layer": "L_STRUCTURE", "geometry": geometry}]},
    )
    assert response.status_code == 201, response.text
    entity_id = response.json()["entities"][0]["id"]
    evidence_id = created_id(
        client.post(f"{API}/case-files/{case_id}/evidence", json={"label": "Vehículo A"})
    )
    stored = FilesystemStorage(tmp_path / "storage").put(io.BytesIO(b"0\nSECTION\n0\nEOF\n"))
    with factory() as db:
        job = db.scalars(select(JobMeta).where(JobMeta.source_id == uuid.UUID(source_id))).first()
        assert job is not None
        artifact = Artifact(
            plan_id=uuid.UUID(plan_id),
            revision_id=uuid.UUID(revision_id),
            kind="dxf",
            is_draft=True,
            sha256=stored.sha256,
            storage_key=stored.key,
            byte_size=16,
            generator_version="authz-matrix",
        )
        db.add(artifact)
        db.commit()
        job_id, artifact_id = str(job.id), str(artifact.id)
    return World(
        case_id=case_id,
        source_id=source_id,
        plan_id=plan_id,
        revision_id=revision_id,
        entity_id=entity_id,
        evidence_id=evidence_id,
        job_id=job_id,
        artifact_id=artifact_id,
    )


def test_route_list_matches_the_app_and_every_exemption_exists() -> None:
    committed = committed_operations()
    generated = operations(create_app().openapi())
    assert {op.key for op in committed} == {op.key for op in generated}, "run export_openapi.py"
    assert stale_exemptions(committed, EXEMPT) == []
    assert stale_exemptions(committed, NOT_FOUND_BY_DESIGN) == []
    unexplained = [op.key for op in matrix_operations(committed, EXEMPT) if not op.params]
    assert unexplained == [], "operations without path parameters must be listed in EXEMPT"
    assert [op.key for op in committed if missing_builders(op)] == []


def test_outsider_is_denied_on_every_path_parameter_route(
    actors: Actors, world: World, factory: sessionmaker[Session]
) -> None:
    ops = committed_operations()
    matrix = matrix_operations(ops, EXEMPT)
    failures: list[str] = []
    for op in matrix:
        failures += check_outsider(op, actors, world, factory)
    verified_params: set[str] = set()
    for op in matrix:
        permitted_failures, checked = check_permitted(op, actors, world)
        failures += permitted_failures
        if checked and not permitted_failures:
            verified_params.update(op.params)
    assert failures == [], "\n".join(failures)

    with_params = [op for op in ops if op.params]
    exempt_with_params = [op for op in with_params if op.key in EXEMPT]
    assert len(matrix) == len(with_params) - len(exempt_with_params)
    used_params = {param for op in matrix for param in op.params}
    assert verified_params == used_params, "every id must be reachable by a permitted user"
    assert len(denied_events(factory, actors.outsider_id)) == len(matrix)


def test_anonymous_requests_get_401_on_every_path_parameter_route(
    app: FastAPI, stack: ExitStack, world: World
) -> None:
    anonymous = stack.enter_context(TestClient(app, base_url="https://testserver"))
    statuses = {
        op.key: anonymous.request(op.method, url_for(op, world)).status_code
        for op in matrix_operations(committed_operations(), EXEMPT)
    }
    assert {key: status for key, status in statuses.items() if status != 401} == {}


def test_mutations_without_the_csrf_header_are_refused_before_authorization(
    app: FastAPI,
    stack: ExitStack,
    factory: sessionmaker[Session],
    owner: User,
    world: World,
) -> None:
    admin = insert_user(factory, UserRole.ADMIN)
    clients = {False: signed_in(app, stack, owner, csrf=False)}
    clients[True] = signed_in(app, stack, admin, csrf=False)
    failures: list[str] = []
    mutations = [op for op in matrix_operations(committed_operations(), EXEMPT) if op.mutating]
    for op in mutations:
        response = clients[op.global_scope].request(op.method, url_for(op, world))
        if response.status_code != 403 or response.json()["detail"] != CSRF_DETAIL:
            failures.append(f"{op.key}: {response.status_code} {response.text}")
    assert failures == [], "\n".join(failures)
    assert denied_events(factory, owner.id) == []
    assert denied_events(factory, admin.id) == []


def test_a_route_without_a_permission_check_fails_the_matrix(
    app: FastAPI, actors: Actors, world: World, factory: sessionmaker[Session]
) -> None:
    def unguarded(plan_id: str) -> dict[str, str]:
        return {"plan_id": plan_id}

    def guarded(
        plan: Annotated[Plan, Depends(require_plan_permission("read"))],
    ) -> dict[str, str]:
        return {"plan_id": str(plan.id)}

    app.add_api_route(f"{API}/plans/{{plan_id}}/unguarded-probe", unguarded, methods=["GET"])
    app.add_api_route(f"{API}/plans/{{plan_id}}/guarded-probe", guarded, methods=["GET"])
    app.openapi_schema = None
    probes = {op.path: op for op in operations(app.openapi()) if "-probe" in op.path}

    unguarded_op = probes[f"{API}/plans/{{plan_id}}/unguarded-probe"]
    assert check_outsider(unguarded_op, actors, world, factory) == [
        f"{unguarded_op.key}: outsider got 200, expected 403 + access.denied"
    ]
    guarded_op = probes[f"{API}/plans/{{plan_id}}/guarded-probe"]
    assert check_outsider(guarded_op, actors, world, factory) == []
    assert check_permitted(guarded_op, actors, world) == ([], True)


def test_a_made_up_id_fails_the_matrix(
    actors: Actors, world: World, factory: sessionmaker[Session]
) -> None:
    op = Operation("GET", f"{API}/plans/{{plan_id}}", ("plan_id",), False)
    fake = replace(world, plan_id=str(uuid.uuid4()))

    assert check_outsider(op, actors, fake, factory) == [
        f"{op.key}: outsider got 404, expected 403 + access.denied"
    ]
    assert check_permitted(op, actors, fake) == (
        [f"{op.key}: permitted user got 404, expected 200"],
        True,
    )


def test_unknown_parameters_and_stale_exemptions_are_reported() -> None:
    op = Operation("GET", f"{API}/widgets/{{widget_id}}", ("widget_id",), False)
    assert missing_builders(op) == ["widget_id"]
    no_params = Operation("GET", f"{API}/widgets", (), False)
    assert matrix_operations([no_params], {}) == [no_params]
    assert stale_exemptions([no_params], {"GET /api/v1/gone": "removed"}) == ["GET /api/v1/gone"]
