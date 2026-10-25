"""Migration 0002 creates source, event and job_meta, and events are immutable (H1-7)."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from sketch2cad.db.session import create_db_engine

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
NEW_TABLES = {"source", "event", "job_meta"}
INITIAL_TABLES = {"user", "session", "case_file", "case_member", "case_code_counter"}
HASH = "0" * 64
REVISION = "0002_sources_events"


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
def schema_engine(database_url: str, migration_schema: str) -> Iterator[Engine]:
    """Engine whose connections use ``migration_schema`` as ``search_path``."""
    engine = create_db_engine(
        database_url, connect_args={"options": f"-csearch_path={migration_schema}"}
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def migrated(migration_schema: str, schema_engine: Engine) -> Engine:
    """``schema_engine`` after ``alembic upgrade 0002_sources_events``."""
    command.upgrade(alembic_config(migration_schema), REVISION)
    return schema_engine


def alembic_config(schema: str) -> Config:
    """Alembic configuration from ``alembic.ini`` targeting ``schema``."""
    config = Config(str(ALEMBIC_INI))
    config.attributes["schema"] = schema
    config.attributes["skip_logging_config"] = True
    return config


def table_names(engine: Engine, schema: str) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
                ),
                {"schema": schema},
            ).scalars()
        )


def function_exists(engine: Engine, schema: str, name: str) -> bool:
    with engine.connect() as connection:
        found = connection.execute(
            text(
                "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = :schema AND p.proname = :name"
            ),
            {"schema": schema, "name": name},
        ).scalar()
    return found is not None


def insert_event(connection: Connection) -> int:
    """Insert a user, a case file and one event; return the event id."""
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
    return connection.execute(
        text(
            "INSERT INTO event (case_file_id, actor_id, actor_role, kind, after, occurred_at, "
            "prev_hash, hash) VALUES (:case_file_id, :user_id, 'technician', "
            "'case_file.created', '{\"code\": \"EXP-2026-0001\"}', now(), :hash, :hash) "
            "RETURNING id"
        ),
        {"case_file_id": case_file_id, "user_id": user_id, "hash": HASH},
    ).scalar_one()


def test_upgrade_creates_the_three_tables(migration_schema: str, migrated: Engine) -> None:
    tables = table_names(migrated, migration_schema)

    assert NEW_TABLES <= tables
    assert tables == NEW_TABLES | INITIAL_TABLES | {"alembic_version"}
    with migrated.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0002_sources_events"


def test_downgrade_one_step_removes_the_three_tables_and_the_trigger_function(
    migration_schema: str, migrated: Engine
) -> None:
    command.downgrade(alembic_config(migration_schema), "-1")

    tables = table_names(migrated, migration_schema)
    assert tables & NEW_TABLES == set()
    assert tables == INITIAL_TABLES | {"alembic_version"}
    assert not function_exists(migrated, migration_schema, "reject_event_mutation")
    with migrated.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0001_initial"


def test_update_on_event_fails_with_a_database_error(migrated: Engine) -> None:
    with migrated.begin() as connection:
        event_id = insert_event(connection)

    with pytest.raises(DBAPIError, match="append-only: UPDATE"), migrated.begin() as connection:
        connection.execute(text("UPDATE event SET reason = 'x'"))

    with migrated.connect() as connection:
        reason = connection.execute(
            text("SELECT reason FROM event WHERE id = :id"), {"id": event_id}
        ).scalar_one()
    assert reason is None


def test_delete_on_event_fails_with_a_database_error(migrated: Engine) -> None:
    with migrated.begin() as connection:
        insert_event(connection)

    with pytest.raises(DBAPIError, match="append-only: DELETE"), migrated.begin() as connection:
        connection.execute(text("DELETE FROM event"))

    with migrated.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM event")).scalar_one() == 1


def test_event_has_a_unique_index_on_case_file_id_and_id(
    migration_schema: str, migrated: Engine
) -> None:
    with migrated.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = :schema AND indexname = 'ix_event_case_file_id_id'"
            ),
            {"schema": migration_schema},
        ).scalar_one()
    assert "CREATE UNIQUE INDEX" in definition
    assert "(case_file_id, id)" in definition


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO job_meta (kind, status) VALUES ('qc', 'done')",
        "INSERT INTO source (case_file_id, original_filename, media_type, byte_size, sha256, "
        "storage_key, uploaded_by) SELECT c.id, 'a.gif', 'image/gif', 1, :hash, 'k', "
        "c.created_by FROM case_file c",
    ],
    ids=["job_status", "source_media_type"],
)
def test_check_constraints_reject_unknown_values(migrated: Engine, statement: str) -> None:
    with migrated.begin() as connection:
        insert_event(connection)

    with pytest.raises(IntegrityError, match="ck_"), migrated.begin() as connection:
        connection.execute(text(statement), {"hash": HASH})


def case_file_id_is_nullable(engine: Engine, schema: str) -> bool:
    with engine.connect() as connection:
        nullable = connection.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_schema = :schema "
                "AND table_name = 'event' AND column_name = 'case_file_id'"
            ),
            {"schema": schema},
        ).scalar_one()
    return nullable == "YES"


def insert_global_event(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO event (kind, after, occurred_at, prev_hash, hash) "
            "VALUES ('auth.failed', '{\"cause\": \"unknown_user\"}', now(), :hash, :hash)"
        ),
        {"hash": HASH},
    )


def test_upgrade_0003_allows_global_events_and_indexes_their_chain(
    migration_schema: str, schema_engine: Engine
) -> None:
    command.upgrade(alembic_config(migration_schema), "0003_global_auth_chain")

    assert case_file_id_is_nullable(schema_engine, migration_schema)
    with schema_engine.begin() as connection:
        insert_global_event(connection)
        definition = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = :schema AND indexname = 'ix_event_global_chain_id'"
            ),
            {"schema": migration_schema},
        ).scalar_one()
    assert "(id) WHERE (case_file_id IS NULL)" in definition


def test_downgrade_0003_restores_not_null_on_an_empty_global_chain(
    migration_schema: str, schema_engine: Engine
) -> None:
    config = alembic_config(migration_schema)
    command.upgrade(config, "0003_global_auth_chain")
    command.downgrade(config, REVISION)

    assert not case_file_id_is_nullable(schema_engine, migration_schema)


def test_downgrade_0003_refuses_while_global_events_exist(
    migration_schema: str, schema_engine: Engine
) -> None:
    config = alembic_config(migration_schema)
    command.upgrade(config, "0003_global_auth_chain")
    with schema_engine.begin() as connection:
        insert_global_event(connection)

    with pytest.raises(RuntimeError, match="global auth chain"):
        command.downgrade(config, REVISION)
    assert case_file_id_is_nullable(schema_engine, migration_schema)
