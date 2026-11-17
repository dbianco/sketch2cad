"""Revision freezing and reopening with canonical snapshots (REQ-10, US-14, H2-8)."""

import hashlib
import importlib.util
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.audit import EventKind
from sketch2cad.cases.service import NewCaseFile, create_case
from sketch2cad.db.base import Base
from sketch2cad.db.models import (
    Calibration,
    CaseFile,
    Entity,
    Event,
    Evidence,
    IncidentType,
    MetricStatus,
    Plan,
    PlanStatus,
    Revision,
    Source,
    SourceMode,
    User,
    UserRole,
)
from sketch2cad.plans.service import NewPlan, create_plan
from sketch2cad.revisions import service
from sketch2cad.revisions.snapshot import (
    SnapshotError,
    build_snapshot,
    canonical_bytes,
    snapshot_sha256,
)

MIGRATION = Path(__file__).resolve().parents[2] / "migrations/versions/0005_plans_editor.py"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
MATRIX = [[0.1, 0.0, -3.25], [0.0, 0.1, 7.5], [0.0, 0.0, 1.0]]
FRAME = {"origin_paper_mm": [12.5, 30.0], "x_axis_angle_deg": 33.7, "north_angle_deg": 15.0}


def _frozen_trigger_sql() -> list[str]:
    """The ``revision_frozen_immutable`` trigger statements of migration 0005."""
    spec = importlib.util.spec_from_file_location("migration_0005", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = module.CREATE_REJECT_FUNCTION.replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"
    )
    return [function, module.CREATE_IMMUTABLE_TRIGGER]


@pytest.fixture(scope="module")
def tables(db_engine: Engine) -> Iterator[None]:
    """Application tables plus the frozen-revision trigger, dropped afterwards."""
    db_engine.dispose()
    Base.metadata.create_all(db_engine)
    with db_engine.begin() as connection:
        for statement in _frozen_trigger_sql():
            connection.execute(text(statement))
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
def user(factory: sessionmaker[Session]) -> User:
    with factory() as db:
        row = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name="Rita Revisora",
            password_hash="not-a-real-hash",  # noqa: S106 - never used to sign in
            role=UserRole.REVIEWER,
        )
        db.add(row)
        db.commit()
        return row


@pytest.fixture
def plan_id(factory: sessionmaker[Session], user: User) -> uuid.UUID:
    """A plan in ``IN_REVIEW`` with draft revision 1 and an active metric calibration."""
    with factory() as db:
        data = NewCaseFile(IncidentType.TRAFFIC_COLLISION, date(2026, 10, 20), "Choque", "Av. 1")
        case_file: CaseFile = create_case(db, user.id, data, NOW)
        source = Source(
            case_file_id=case_file.id,
            original_filename="croquis.png",
            media_type="image/png",
            byte_size=1024,
            sha256=uuid.uuid4().hex * 2,
            storage_key=f"sources/{uuid.uuid4().hex}",
            uploaded_by=user.id,
        )
        db.add(source)
        db.flush()
        new_plan = NewPlan("Plano", source.id, SourceMode.SCALED_DRAWING)
        plan, _ = create_plan(db, case_file, user, new_plan)
        plan.status = PlanStatus.IN_REVIEW
        plan.metric_status = MetricStatus.METRIC
        db.add(
            Calibration(
                plan_id=plan.id,
                method="photo_homography_4pt",
                image_to_paper=MATRIX,
                control_points=[{"image_px": [10, 20], "paper_mm": [0.0, 0.0]}],
                reprojection_error_mm={"max": 0.42, "rms": 0.21},
                paper_scale=100,
                local_frame=FRAME,
                as_metric=True,
                accepted_by=user.id,
                accepted_at=NOW,
            )
        )
        db.commit()
        return plan.id


def entity(revision_id: uuid.UUID | None, index: int, **overrides: Any) -> Entity:
    """An entity with unordered nested keys and numbers that stress the JSONB round trip."""
    values: dict[str, Any] = {
        "id": uuid.UUID(int=(index + 1) * 0x1_0000_0001),
        "revision_id": revision_id,
        "type": "polyline",
        "layer": "L_STRUCTURE",
        "geometry": {
            "kind": "polyline",
            "coords_m": [[0.1 + 0.2, 1e-7], [4.2 * index, -0.0], [123456.789, 3.0]],
            "closed": index % 2 == 0,
        },
        "image_region": {"source_id": "src_1", "polygon_px": [[120, 340], [980, 338]]},
        "attributes": {"zeta": "último", "alpha": {"b": 2, "a": [1, "señal é \U0001f697"]}},
        "evidence_id": None,
        "constraint": None,
        "source": {"method": "cv", "job_id": "job_7", "parent_entity_id": None},
        "confidence": 0.8125,
        "model_version": "cv-0.1",
        "review_status": "accepted",
        "metric_origin": "scaled_drawing",
        "created_by": None,
        "updated_at": NOW,
        "deleted_at": None,
    }
    values.update(overrides)
    return Entity(**values)


def load(db: Session, plan_id: uuid.UUID) -> tuple[Plan, Revision]:
    plan = db.get(Plan, plan_id)
    assert plan is not None and plan.current_revision_id is not None
    revision = db.get(Revision, plan.current_revision_id)
    assert revision is not None
    return plan, revision


def events(db: Session, kind: EventKind) -> list[Event]:
    return list(db.scalars(select(Event).where(Event.kind == kind.value).order_by(Event.id)))


# --- Pure snapshot -------------------------------------------------------------------------


def pure_inputs() -> tuple[Plan, Revision]:
    plan = Plan(
        id=uuid.UUID(int=7),
        source_mode=SourceMode.SCHEMATIC_SKETCH,
        metric_status=MetricStatus.APPROXIMATE,
    )
    return plan, Revision(number=3)


@given(st.permutations(range(6)))
def test_insertion_order_does_not_change_the_snapshot_hash(order: list[int]) -> None:
    plan, revision = pure_inputs()
    entities = [entity(None, index) for index in range(6)]
    shuffled = [entity(None, index) for index in order]

    expected = snapshot_sha256(build_snapshot(plan, revision, None, "default-1", entities))
    snapshot = build_snapshot(plan, revision, None, "default-1", shuffled)

    assert snapshot_sha256(snapshot) == expected
    assert [e["id"] for e in snapshot["entities"]] == sorted(str(e.id) for e in entities)


def test_snapshot_has_the_section_8_3_shape_and_leaves_out_deleted_entities() -> None:
    plan, revision = pure_inputs()
    live, deleted = entity(None, 0), entity(None, 1, deleted_at=NOW)

    snapshot = build_snapshot(plan, revision, None, "default-1", [deleted, live])

    assert list(snapshot) == [
        "schema_version",
        "plan_id",
        "revision_number",
        "source_mode",
        "metric_status",
        "calibration",
        "local_frame",
        "catalog_version",
        "entities",
        "evidence_table",
    ]
    assert snapshot["schema_version"] == "2.0"
    assert snapshot["plan_id"] == str(plan.id)
    assert snapshot["revision_number"] == 3
    assert (snapshot["source_mode"], snapshot["metric_status"]) == (
        "schematic_sketch",
        "approximate",
    )
    assert [e["id"] for e in snapshot["entities"]] == [str(live.id)]
    assert snapshot["entities"][0]["revision_number"] == 3
    assert "deleted_at" not in snapshot["entities"][0]
    assert canonical_bytes(snapshot).startswith(
        b'{"calibration":null,"catalog_version":"default-1"'
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2.0**53, -(2**60)])
def test_numbers_that_cannot_round_trip_are_rejected(value: float) -> None:
    plan, revision = pure_inputs()
    bad = entity(None, 0, attributes={"n": value})

    with pytest.raises(SnapshotError, match=r"\$\.entities\[0\]\.attributes\.n"):
        build_snapshot(plan, revision, None, "default-1", [bad])


def evidence_point(
    index: int, evidence: Evidence, revision_id: uuid.UUID | None = None, **overrides: Any
) -> Entity:
    geometry = {"kind": "point", "coords_m": [[1.25 * index, -0.5]]}
    values = {"type": "point", "layer": "L_EVIDENCE", "geometry": geometry}
    return entity(revision_id, index, evidence_id=evidence.id, **{**values, **overrides})


def test_evidence_table_lists_placed_evidence_by_number() -> None:
    plan, revision = pure_inputs()
    seven = Evidence(id=uuid.UUID(int=70), number=7, label="Casquillo")
    two = Evidence(id=uuid.UUID(int=20), number=2, label="Mancha")
    unplaced = Evidence(id=uuid.UUID(int=90), number=1, label="Sin ubicar")
    symbol = entity(
        None,
        3,
        type="symbol",
        layer="L_SYMBOLS",
        geometry={"kind": "symbol", "anchor_m": [4.5, 2.0], "symbol_id": "tree"},
        evidence_id=two.id,
        metric_origin="triangulation",
    )
    entities = [
        evidence_point(1, seven),
        symbol,
        evidence_point(2, two, deleted_at=NOW),
        entity(None, 4),
    ]

    table = build_snapshot(plan, revision, None, "default-1", entities, [seven, two, unplaced])[
        "evidence_table"
    ]

    assert table == [
        {
            "evidence_id": str(two.id),
            "evidence_number": 2,
            "entity_id": str(symbol.id),
            "label": "Mancha",
            "x_m": 4.5,
            "y_m": 2.0,
            "metric_origin": "triangulation",
        },
        {
            "evidence_id": str(seven.id),
            "evidence_number": 7,
            "entity_id": str(entities[0].id),
            "label": "Casquillo",
            "x_m": 1.25,
            "y_m": -0.5,
            "metric_origin": "scaled_drawing",
        },
    ]


def test_evidence_of_another_case_file_is_rejected() -> None:
    plan, revision = pure_inputs()
    foreign = Evidence(id=uuid.UUID(int=70), number=7, label="Casquillo")

    with pytest.raises(SnapshotError, match="evidence"):
        build_snapshot(plan, revision, None, "default-1", [evidence_point(1, foreign)])


# --- Freeze --------------------------------------------------------------------------------


def test_freeze_fills_the_evidence_table_from_the_case_register(
    factory: sessionmaker[Session], plan_id: uuid.UUID, user: User
) -> None:
    with factory() as db:
        plan, revision = load(db, plan_id)
        found = Evidence(
            case_file_id=plan.case_file_id, number=1, label="Huella", created_by=user.id
        )
        db.add(found)
        db.flush()
        point = evidence_point(5, found, revision.id)
        db.add(point)
        db.flush()
        frozen = service.freeze(db, revision, user)
        db.commit()
        snapshot, digest = frozen.snapshot, frozen.snapshot_sha256

    assert snapshot is not None
    assert snapshot["evidence_table"] == [
        {
            "evidence_id": str(found.id),
            "evidence_number": 1,
            "entity_id": str(point.id),
            "label": "Huella",
            "x_m": 6.25,
            "y_m": -0.5,
            "metric_origin": "scaled_drawing",
        }
    ]
    assert snapshot_sha256(snapshot) == digest


def test_frozen_snapshot_read_back_from_the_database_has_identical_bytes(
    factory: sessionmaker[Session], plan_id: uuid.UUID, user: User
) -> None:
    with factory() as db:
        plan, revision = load(db, plan_id)
        db.add_all([entity(revision.id, i) for i in (4, 1, 3)])
        db.add(entity(revision.id, 2, deleted_at=NOW))
        db.flush()
        first = canonical_bytes(service.revision_snapshot(db, plan, revision))
        frozen = service.freeze(db, revision, user)
        db.commit()
        revision_id, digest = frozen.id, frozen.snapshot_sha256

    with factory() as db:
        stored = db.get(Revision, revision_id)
        assert stored is not None and stored.snapshot is not None
        raw = db.execute(
            text("SELECT snapshot::text FROM revision WHERE id = :id"), {"id": revision_id}
        ).scalar_one()
        frozen_events = events(db, EventKind.REVISION_FROZEN)

    assert stored.state == "frozen" and stored.frozen_at is not None
    assert canonical_bytes(stored.snapshot) == first
    assert canonical_bytes(json.loads(raw)) == first
    assert digest == stored.snapshot_sha256 == hashlib.sha256(first).hexdigest()
    snapshot = stored.snapshot
    assert [e["id"] for e in snapshot["entities"]] == [
        str(uuid.UUID(int=i * 0x1_0000_0001)) for i in (2, 4, 5)
    ]
    assert snapshot["calibration"]["image_to_paper"] == MATRIX
    assert snapshot["calibration"]["paper_scale"] == 100
    assert snapshot["calibration"]["accepted_at"] == "2026-10-25T12:00:00.000000Z"
    assert snapshot["local_frame"] == FRAME
    assert snapshot["catalog_version"] == "default-1"
    assert len(frozen_events) == 1
    event = frozen_events[0]
    assert (event.plan_id, event.revision_id, event.actor_id) == (plan_id, revision_id, user.id)
    assert event.after == {"revision_number": 1, "snapshot_sha256": digest, "entity_count": 3}


def test_physical_insertion_order_does_not_change_the_stored_hash(
    factory: sessionmaker[Session], plan_id: uuid.UUID
) -> None:
    digests: list[str] = []
    for order in ([0, 1, 2, 3], [3, 1, 0, 2]):
        with factory() as db:
            plan, revision = load(db, plan_id)
            db.execute(text("DELETE FROM entity WHERE revision_id = :id"), {"id": revision.id})
            for index in order:
                db.add(entity(revision.id, index))
                db.flush()
            digests.append(snapshot_sha256(service.revision_snapshot(db, plan, revision)))
            db.commit()

    assert digests[0] == digests[1]


def test_only_the_current_draft_can_be_frozen_and_a_frozen_row_is_immutable(
    factory: sessionmaker[Session], plan_id: uuid.UUID, user: User
) -> None:
    with factory() as db:
        _, revision = load(db, plan_id)
        service.freeze(db, revision, user)
        db.commit()
        with pytest.raises(service.RevisionConflictError, match="already frozen"):
            service.freeze(db, revision, user)
        db.rollback()
        with pytest.raises(DBAPIError, match="is frozen"):
            db.execute(
                text("UPDATE revision SET snapshot_sha256 = :h WHERE id = :id"),
                {"h": "0" * 64, "id": revision.id},
            )
        db.rollback()

        reopened = service.reopen(db, load(db, plan_id)[0], user)
        db.commit()
        stale = Revision(plan_id=plan_id, number=99, created_by=user.id)
        db.add(stale)
        db.flush()
        with pytest.raises(service.RevisionConflictError, match="not the current revision"):
            service.freeze(db, stale, user)
        db.rollback()
        assert reopened.revision.state == "draft"


# --- Reopen --------------------------------------------------------------------------------


def test_reopen_creates_the_next_draft_and_keeps_the_frozen_revision(
    factory: sessionmaker[Session], plan_id: uuid.UUID, user: User
) -> None:
    with factory() as db:
        plan, first = load(db, plan_id)
        anchor = entity(first.id, 0, review_status="corrected", metric_origin="triangulation")
        tied = entity(
            first.id,
            1,
            constraint={"kind": "triangulation", "refs": [str(anchor.id)], "params": {}},
            source={"method": "derived", "job_id": None, "parent_entity_id": str(anchor.id)},
            created_by=user.id,
        )
        db.add_all([anchor, tied, entity(first.id, 2, deleted_at=NOW)])
        db.flush()
        service.freeze(db, first, user)
        db.commit()
        first_id, first_hash, first_snapshot = first.id, first.snapshot_sha256, first.snapshot

        result = service.reopen(db, plan, user)
        db.commit()
        child_id = result.revision.id

    with factory() as db:
        plan, child = load(db, plan_id)
        parent = db.get(Revision, first_id)
        assert parent is not None
        copies = list(db.scalars(select(Entity).where(Entity.revision_id == child_id)))
        originals = list(db.scalars(select(Entity).where(Entity.revision_id == first_id)))
        reopened_events = events(db, EventKind.REVISION_REOPENED)

    assert child.id == child_id == plan.current_revision_id
    assert (child.number, child.parent_revision_id, child.state) == (2, first_id, "draft")
    assert child.snapshot is None and child.snapshot_sha256 is None
    assert parent.state == "frozen" and parent.snapshot_sha256 == first_hash
    assert parent.snapshot == first_snapshot and len(originals) == 3
    assert set(result.entity_ids) == {anchor.id, tied.id}
    by_id = {copy.id: copy for copy in copies}
    assert set(by_id) == set(result.entity_ids.values())
    assert set(by_id).isdisjoint(original.id for original in originals)
    new_anchor, new_tied = by_id[result.entity_ids[anchor.id]], by_id[result.entity_ids[tied.id]]
    assert (new_anchor.review_status, new_anchor.metric_origin) == ("corrected", "triangulation")
    assert new_tied.constraint == {
        "kind": "triangulation",
        "refs": [str(new_anchor.id)],
        "params": {},
    }
    assert new_tied.source == tied.source and new_tied.created_by == user.id
    assert (new_tied.confidence, new_tied.model_version) == (0.8125, "cv-0.1")
    assert first_snapshot is not None
    assert new_tied.geometry == first_snapshot["entities"][1]["geometry"]
    assert len(reopened_events) == 1 and reopened_events[0].revision_id == child_id
    assert reopened_events[0].after is not None
    assert reopened_events[0].after["parent_revision_id"] == str(first_id)


def test_reopen_requires_a_frozen_current_revision(
    factory: sessionmaker[Session], plan_id: uuid.UUID, user: User
) -> None:
    with factory() as db:
        plan, _ = load(db, plan_id)
        with pytest.raises(service.RevisionConflictError, match="frozen"):
            service.reopen(db, plan, user)
        assert events(db, EventKind.REVISION_REOPENED) == []
