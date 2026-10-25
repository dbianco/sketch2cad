"""Migration 0008 adds the evidence register and links entities to it (H3)."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sketch2cad.db.base import Base
from sketch2cad.db.models import Entity, Evidence
from sketch2cad.db.session import create_db_engine

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
REVISION = "0008_evidence_constraints"
PREVIOUS_REVISION = "0007_calibration_as_metric"
HASH = "0" * 64


@pytest.fixture
def migration_schema(database_url: str) -> Iterator[str]:
    """An empty schema dedicated to one migration test, dropped afterwards."""
    schema = f"test_migration_{uuid.uuid4().hex}"
    engine = create_db_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield schema
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def migrated(database_url: str, migration_schema: str) -> Iterator[Engine]:
    """Engine on ``migration_schema`` after ``alembic upgrade 0008_evidence_constraints``."""
    command.upgrade(alembic_config(migration_schema), REVISION)
    engine = create_db_engine(
        database_url, connect_args={"options": f"-csearch_path={migration_schema}"}
    )
    try:
        yield engine
    finally:
        engine.dispose()


def alembic_config(schema: str) -> Config:
    """Alembic configuration from ``alembic.ini`` targeting ``schema``."""
    config = Config(str(ALEMBIC_INI))
    config.attributes["schema"] = schema
    config.attributes["skip_logging_config"] = True
    return config


def insert_case_file(
    session: Session, code: str, user_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Insert a case file (and its creator when ``user_id`` is None); return both ids."""
    if user_id is None:
        user_id = session.execute(
            text(
                'INSERT INTO "user" (username, password_hash, role) '
                "VALUES (:name, 'hash', 'technician') RETURNING id"
            ),
            {"name": f"tech-{code}"},
        ).scalar_one()
    case_file_id = session.execute(
        text(
            "INSERT INTO case_file (code, incident_type, incident_date, description, "
            "declared_location, created_by) VALUES (:code, 'inspection', '2026-10-25', "
            "'test', 'test', :user_id) RETURNING id"
        ),
        {"code": code, "user_id": user_id},
    ).scalar_one()
    return {"user_id": user_id, "case_file_id": case_file_id}


def insert_revision(session: Session, ids: dict[str, Any]) -> uuid.UUID:
    """Insert a source, a plan and a draft revision in the case file; return the revision id."""
    source_id = session.execute(
        text(
            "INSERT INTO source (case_file_id, original_filename, media_type, byte_size, "
            "sha256, storage_key, uploaded_by) VALUES (:case_file_id, 'sketch.png', "
            "'image/png', 1, :hash, 'key', :user_id) RETURNING id"
        ),
        {**ids, "hash": HASH},
    ).scalar_one()
    plan_id = session.execute(
        text(
            "INSERT INTO plan (case_file_id, title, primary_source_id) "
            "VALUES (:case_file_id, 'Plan', :source_id) RETURNING id"
        ),
        {**ids, "source_id": source_id},
    ).scalar_one()
    return session.execute(
        text(
            "INSERT INTO revision (plan_id, number, created_by) "
            "VALUES (:plan_id, 1, :user_id) RETURNING id"
        ),
        {"plan_id": plan_id, "user_id": ids["user_id"]},
    ).scalar_one()


def new_evidence(ids: dict[str, Any], number: int) -> Evidence:
    return Evidence(
        case_file_id=ids["case_file_id"],
        number=number,
        label=f"Evidence {number}",
        created_by=ids["user_id"],
    )


def new_point(revision_id: uuid.UUID, **fields: Any) -> Entity:
    return Entity(
        revision_id=revision_id,
        type="point",
        layer="L_EVIDENCE",
        geometry={"kind": "point", "coords_m": [[1.0, 2.0]]},
        source={"method": "manual"},
        review_status="accepted",
        metric_origin="none",
        **fields,
    )


def test_same_number_twice_in_one_case_file_fails(migrated: Engine) -> None:
    with Session(migrated) as session, session.begin():
        ids = insert_case_file(session, "EXP-2026-0001")
        session.add(new_evidence(ids, 1))

    with pytest.raises(IntegrityError, match="uq_evidence_case_file_id_number"):
        with Session(migrated) as session, session.begin():
            session.add(new_evidence(ids, 1))

    with Session(migrated) as session:
        assert session.execute(text("SELECT count(*) FROM evidence")).scalar_one() == 1


def test_same_number_in_two_case_files_succeeds(migrated: Engine) -> None:
    with Session(migrated) as session, session.begin():
        first = insert_case_file(session, "EXP-2026-0001")
        second = insert_case_file(session, "EXP-2026-0002", first["user_id"])
        session.add_all([new_evidence(first, 1), new_evidence(second, 1)])

    with Session(migrated) as session:
        rows = session.execute(text("SELECT number, attributes FROM evidence")).all()
    assert [tuple(row) for row in rows] == [(1, {}), (1, {})]


def test_evidence_number_starts_at_one(migrated: Engine) -> None:
    with pytest.raises(IntegrityError, match="ck_evidence_number"):
        with Session(migrated) as session, session.begin():
            session.add(new_evidence(insert_case_file(session, "EXP-2026-0001"), 0))


def test_entity_references_existing_evidence(migrated: Engine) -> None:
    with Session(migrated) as session, session.begin():
        ids = insert_case_file(session, "EXP-2026-0001")
        evidence = new_evidence(ids, 7)
        session.add(evidence)
        session.flush()
        evidence_id = evidence.id
        entity = new_point(insert_revision(session, ids), evidence_id=evidence_id)
        session.add(entity)
        session.flush()
        stored = session.execute(
            text("SELECT evidence_id FROM entity WHERE id = :id"), {"id": entity.id}
        ).scalar_one()
    assert stored == evidence_id


def test_entity_rejects_unknown_evidence_id(migrated: Engine) -> None:
    with Session(migrated) as session, session.begin():
        revision_id = insert_revision(session, insert_case_file(session, "EXP-2026-0001"))

    with pytest.raises(IntegrityError, match="fk_entity_evidence_id_evidence"):
        with Session(migrated) as session, session.begin():
            session.add(new_point(revision_id, evidence_id=uuid.uuid4()))


def test_referenced_evidence_cannot_be_deleted(migrated: Engine) -> None:
    with Session(migrated) as session, session.begin():
        ids = insert_case_file(session, "EXP-2026-0001")
        evidence = new_evidence(ids, 1)
        session.add(evidence)
        session.flush()
        session.add(new_point(insert_revision(session, ids), evidence_id=evidence.id))
        evidence_id = evidence.id

    with pytest.raises(IntegrityError, match="fk_entity_evidence_id_evidence"):
        with Session(migrated) as session, session.begin():
            session.execute(text("DELETE FROM evidence WHERE id = :id"), {"id": evidence_id})


def test_constraint_kind_is_checked_and_null_stays_valid(migrated: Engine) -> None:
    refs = [str(uuid.uuid4()), str(uuid.uuid4())]
    triangulation = {
        "kind": "triangulation",
        "refs": refs,
        "params": {"d_a_m": 2.1, "d_b_m": 3.45, "solution": "nearest"},
    }
    with Session(migrated) as session, session.begin():
        revision_id = insert_revision(session, insert_case_file(session, "EXP-2026-0001"))
        session.add_all([new_point(revision_id), new_point(revision_id, constraint=triangulation)])

    with pytest.raises(IntegrityError, match="ck_entity_constraint_kind"):
        with Session(migrated) as session, session.begin():
            session.add(new_point(revision_id, constraint={"kind": "circle", "refs": refs}))


def test_downgrade_one_step_removes_the_evidence_objects(
    migration_schema: str, migrated: Engine
) -> None:
    command.downgrade(alembic_config(migration_schema), "-1")

    with migrated.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        evidence_table = connection.execute(text("SELECT to_regclass('evidence')")).scalar_one()
        constraints = connection.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'entity'::regclass "
                "AND conname IN ('fk_entity_evidence_id_evidence', 'ck_entity_constraint_kind')"
            )
        ).all()
        evidence_index = connection.execute(
            text("SELECT to_regclass('ix_entity_evidence_id')")
        ).scalar_one()
    assert version == PREVIOUS_REVISION
    assert evidence_table is None
    assert constraints == []
    assert evidence_index is None


def test_models_match_the_migrated_schema(migration_schema: str, migrated: Engine) -> None:
    """No drift between ``Base.metadata`` and the schema built by the migrations."""
    command.upgrade(alembic_config(migration_schema), "head")

    with migrated.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        differences = compare_metadata(context, Base.metadata)

    assert differences == []
