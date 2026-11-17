"""Migration 0005 creates the plans editor schema; frozen revisions are immutable (H2)."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from sketch2cad.db.models import PlanStatus
from sketch2cad.db.session import create_db_engine

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
REVISION = "0005_plans_editor"
NEW_TABLES = {"plan", "calibration", "revision", "entity", "catalog", "artifact"}
NEW_ENUM_TYPES = {"plan_status", "source_mode", "metric_status"}
SPEC_PLAN_STATES = [
    "CREATED",
    "CALIBRATION_PENDING",
    "EDITING",
    "REVIEW_PENDING",
    "IN_REVIEW",
    "VALIDATED",
    "EXPORTED",
    "REJECTED",
    "REOPENED",
]
"""The nine plan states of spec section 9.2."""
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
    """Engine on ``migration_schema`` after ``alembic upgrade 0005_plans_editor``."""
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


def scalars(engine: Engine, query: str, schema: str) -> list[str]:
    with engine.connect() as connection:
        return list(connection.execute(text(query), {"schema": schema}).scalars())


def table_names(engine: Engine, schema: str) -> set[str]:
    query = (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
    )
    return set(scalars(engine, query, schema))


def enum_type_names(engine: Engine, schema: str) -> set[str]:
    query = (
        "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = :schema AND t.typtype = 'e'"
    )
    return set(scalars(engine, query, schema))


def insert_plan(connection: Connection) -> dict[str, uuid.UUID]:
    """Insert a user, a case file, a source and a plan; return their ids."""
    user_id = connection.execute(
        text(
            'INSERT INTO "user" (username, password_hash, role) '
            "VALUES ('tech', 'hash', 'technician') RETURNING id"
        )
    ).scalar_one()
    case_file_id = connection.execute(
        text(
            "INSERT INTO case_file (code, incident_type, incident_date, description, "
            "declared_location, created_by) VALUES ('EXP-2026-0001', 'inspection', "
            "'2026-10-25', 'test', 'test', :user_id) RETURNING id"
        ),
        {"user_id": user_id},
    ).scalar_one()
    source_id = connection.execute(
        text(
            "INSERT INTO source (case_file_id, original_filename, media_type, byte_size, "
            "sha256, storage_key, uploaded_by) VALUES (:case_file_id, 'sketch.png', "
            "'image/png', 1, :hash, 'key', :user_id) RETURNING id"
        ),
        {"case_file_id": case_file_id, "user_id": user_id, "hash": HASH},
    ).scalar_one()
    plan_id = connection.execute(
        text(
            "INSERT INTO plan (case_file_id, title, primary_source_id) "
            "VALUES (:case_file_id, 'Plan', :source_id) RETURNING id"
        ),
        {"case_file_id": case_file_id, "source_id": source_id},
    ).scalar_one()
    return {"user_id": user_id, "plan_id": plan_id}


def insert_revision(connection: Connection, ids: dict[str, uuid.UUID], number: int) -> uuid.UUID:
    """Insert a draft revision ``number`` of the plan and return its id."""
    return connection.execute(
        text(
            "INSERT INTO revision (plan_id, number, created_by) "
            "VALUES (:plan_id, :number, :user_id) RETURNING id"
        ),
        {**ids, "number": number},
    ).scalar_one()


def freeze(connection: Connection, revision_id: uuid.UUID) -> None:
    connection.execute(
        text(
            "UPDATE revision SET state = 'frozen', snapshot = '{\"revision_number\": 1}', "
            "snapshot_sha256 = :hash, frozen_at = now() WHERE id = :id"
        ),
        {"id": revision_id, "hash": HASH},
    )


@pytest.fixture
def frozen_revision(migrated: Engine) -> uuid.UUID:
    """A revision frozen through the allowed ``draft`` to ``frozen`` update."""
    with migrated.begin() as connection:
        revision_id = insert_revision(connection, insert_plan(connection), 1)
        freeze(connection, revision_id)
    return revision_id


def test_upgrade_creates_the_tables_and_enums(migration_schema: str, migrated: Engine) -> None:
    assert NEW_TABLES <= table_names(migrated, migration_schema)
    assert NEW_ENUM_TYPES <= enum_type_names(migrated, migration_schema)


def test_downgrade_one_step_removes_every_new_object(
    migration_schema: str, migrated: Engine
) -> None:
    command.downgrade(alembic_config(migration_schema), "-1")

    assert table_names(migrated, migration_schema) & NEW_TABLES == set()
    assert enum_type_names(migrated, migration_schema) & NEW_ENUM_TYPES == set()
    functions = scalars(
        migrated,
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = :schema",
        migration_schema,
    )
    assert "reject_frozen_revision_mutation" not in functions
    with migrated.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0004_source_raster"


def test_plan_status_enum_has_exactly_the_nine_states_of_the_spec(
    migration_schema: str, migrated: Engine
) -> None:
    labels = scalars(
        migrated,
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = :schema AND t.typname = 'plan_status' ORDER BY e.enumsortorder",
        migration_schema,
    )

    assert labels == SPEC_PLAN_STATES
    assert [status.value for status in PlanStatus] == SPEC_PLAN_STATES


def test_new_plan_defaults_to_created_schematic_and_non_metric(migrated: Engine) -> None:
    with migrated.begin() as connection:
        plan_id = insert_plan(connection)["plan_id"]
        row = connection.execute(
            text("SELECT status, source_mode, metric_status FROM plan WHERE id = :id"),
            {"id": plan_id},
        ).one()

    assert tuple(row) == ("CREATED", "schematic_sketch", "non_metric")


def test_updating_a_frozen_revision_snapshot_fails_with_a_database_error(
    migrated: Engine, frozen_revision: uuid.UUID
) -> None:
    with pytest.raises(DBAPIError, match="is frozen: UPDATE"), migrated.begin() as connection:
        connection.execute(
            text("UPDATE revision SET snapshot = '{\"tampered\": true}' WHERE id = :id"),
            {"id": frozen_revision},
        )

    with migrated.connect() as connection:
        snapshot = connection.execute(
            text("SELECT snapshot FROM revision WHERE id = :id"), {"id": frozen_revision}
        ).scalar_one()
    assert snapshot == {"revision_number": 1}


def test_deleting_a_frozen_revision_fails_with_a_database_error(
    migrated: Engine, frozen_revision: uuid.UUID
) -> None:
    with pytest.raises(DBAPIError, match="is frozen: DELETE"), migrated.begin() as connection:
        connection.execute(text("DELETE FROM revision WHERE id = :id"), {"id": frozen_revision})

    with migrated.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM revision")).scalar_one() == 1


def test_draft_revisions_can_be_updated_and_deleted(migrated: Engine) -> None:
    with migrated.begin() as connection:
        revision_id = insert_revision(connection, insert_plan(connection), 1)
        connection.execute(
            text("UPDATE revision SET number = 2 WHERE id = :id"), {"id": revision_id}
        )
        connection.execute(text("DELETE FROM revision WHERE id = :id"), {"id": revision_id})
        assert connection.execute(text("SELECT count(*) FROM revision")).scalar_one() == 0


def test_freezing_requires_snapshot_hash_and_timestamp(migrated: Engine) -> None:
    with migrated.begin() as connection:
        revision_id = insert_revision(connection, insert_plan(connection), 1)

    with pytest.raises(IntegrityError, match="ck_revision_frozen_snapshot"):
        with migrated.begin() as connection:
            connection.execute(
                text("UPDATE revision SET state = 'frozen' WHERE id = :id"), {"id": revision_id}
            )


def insert_calibration(connection: Connection, ids: dict[str, uuid.UUID], active: bool) -> None:
    connection.execute(
        text(
            "INSERT INTO calibration (plan_id, method, is_active, accepted_by) "
            "VALUES (:plan_id, 'none', :active, :user_id)"
        ),
        {**ids, "active": active},
    )


def test_a_plan_has_at_most_one_active_calibration(migrated: Engine) -> None:
    with migrated.begin() as connection:
        ids = insert_plan(connection)
        insert_calibration(connection, ids, active=True)
        insert_calibration(connection, ids, active=False)

    with pytest.raises(IntegrityError, match="ux_calibration_plan_id_active"):
        with migrated.begin() as connection:
            insert_calibration(connection, ids, active=True)


def test_only_one_catalog_can_be_active(migrated: Engine) -> None:
    insert = text(
        "INSERT INTO catalog (version, is_active, layers, symbols) "
        "VALUES (:version, :active, '[]', '[]')"
    )
    with migrated.begin() as connection:
        connection.execute(insert, {"version": "default-1", "active": True})
        connection.execute(insert, {"version": "draft-2", "active": False})

    with pytest.raises(IntegrityError, match="ux_catalog_active"), migrated.begin() as connection:
        connection.execute(insert, {"version": "other-3", "active": True})


def test_entity_is_soft_deleted_through_deleted_at(migrated: Engine) -> None:
    with migrated.begin() as connection:
        revision_id = insert_revision(connection, insert_plan(connection), 1)
        entity_id = connection.execute(
            text(
                "INSERT INTO entity (revision_id, type, layer, geometry, source, review_status, "
                "metric_origin) VALUES (:revision_id, 'point', 'L_EVIDENCE', "
                '\'{"kind": "point", "coords_m": [[0, 0]]}\', \'{"method": "manual"}\', '
                "'accepted', 'none') RETURNING id"
            ),
            {"revision_id": revision_id},
        ).scalar_one()
        connection.execute(
            text("UPDATE entity SET deleted_at = now() WHERE id = :id"), {"id": entity_id}
        )
        deleted_at = connection.execute(
            text("SELECT deleted_at FROM entity WHERE id = :id"), {"id": entity_id}
        ).scalar_one()

    assert deleted_at is not None


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO entity (revision_id, type, layer, geometry, source, review_status, "
            "metric_origin) SELECT id, 'circle', 'L_TEXT', '{}', '{}', 'accepted', 'none' "
            "FROM revision",
            "ck_entity_type",
        ),
        (
            "INSERT INTO entity (revision_id, type, layer, geometry, source, review_status, "
            "metric_origin, confidence) SELECT id, 'line', 'L_TEXT', '{}', '{}', 'suggested', "
            "'trace_only', 1.5 FROM revision",
            "ck_entity_confidence",
        ),
        (
            "INSERT INTO calibration (plan_id, method, accepted_by) "
            "SELECT plan_id, 'guess', created_by FROM revision",
            "ck_calibration_method",
        ),
        (
            "INSERT INTO artifact (plan_id, kind, is_draft, sha256, storage_key, byte_size, "
            "generator_version) SELECT plan_id, 'docx', true, :hash, 'k', 1, '0' FROM revision",
            "ck_artifact_kind",
        ),
    ],
    ids=["entity_type", "entity_confidence", "calibration_method", "artifact_kind"],
)
def test_check_constraints_reject_unknown_values(
    migrated: Engine, statement: str, constraint: str
) -> None:
    with migrated.begin() as connection:
        insert_revision(connection, insert_plan(connection), 1)

    with pytest.raises(IntegrityError, match=constraint), migrated.begin() as connection:
        connection.execute(text(statement), {"hash": HASH})


def test_event_references_plans_by_id_without_a_foreign_key(migrated: Engine) -> None:
    """The append-only log must not block or depend on the rows it mentions."""
    with migrated.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO event (plan_id, revision_id, entity_id, kind, occurred_at, "
                "prev_hash, hash) VALUES (:id, :id, :id, 'plan.created', now(), :hash, :hash)"
            ),
            {"id": uuid.uuid4(), "hash": HASH},
        )
        assert connection.execute(text("SELECT count(*) FROM event")).scalar_one() == 1
