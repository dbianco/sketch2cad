"""The initial Alembic migration creates and removes the expected schema."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from sketch2cad.db.session import create_db_engine

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
APPLICATION_TABLES = {
    "user",
    "session",
    "case_file",
    "case_member",
    "source",
    "event",
    "job_meta",
}
SUPPORT_TABLES = {"alembic_version", "case_code_counter"}
ENUM_TYPES = {"user_role", "incident_type"}


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


def enum_type_names(engine: Engine, schema: str) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT t.typname FROM pg_type t "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = :schema AND t.typtype = 'e'"
                ),
                {"schema": schema},
            ).scalars()
        )


def test_upgrade_creates_exactly_the_application_tables(
    migration_schema: str, schema_engine: Engine
) -> None:
    command.upgrade(alembic_config(migration_schema), "head")

    tables = table_names(schema_engine, migration_schema)
    assert tables - SUPPORT_TABLES == APPLICATION_TABLES
    assert tables == APPLICATION_TABLES | SUPPORT_TABLES
    assert enum_type_names(schema_engine, migration_schema) == ENUM_TYPES


def test_downgrade_base_removes_every_object(migration_schema: str, schema_engine: Engine) -> None:
    config = alembic_config(migration_schema)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    tables = table_names(schema_engine, migration_schema)
    assert tables & APPLICATION_TABLES == set()
    assert tables - {"alembic_version"} == set()
    assert enum_type_names(schema_engine, migration_schema) == set()
    with schema_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 0


def test_case_file_code_is_unique_and_legal_hold_defaults_to_false(
    migration_schema: str, schema_engine: Engine
) -> None:
    command.upgrade(alembic_config(migration_schema), "head")

    with schema_engine.connect() as connection:
        unique_columns = connection.execute(
            text(
                "SELECT a.attname FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
                "WHERE n.nspname = :schema AND c.conrelid = 'case_file'::regclass "
                "AND c.contype = 'u' AND c.conname = 'uq_case_file_code'"
            ),
            {"schema": migration_schema},
        ).scalars()
        assert list(unique_columns) == ["code"]

        legal_hold = connection.execute(
            text(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'case_file' "
                "AND column_name = 'legal_hold'"
            ),
            {"schema": migration_schema},
        ).one()
        assert legal_hold.column_default == "false"
        assert legal_hold.is_nullable == "NO"
