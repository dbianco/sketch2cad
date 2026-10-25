"""Migration 0006 seeds ``default-1`` once, as the active catalog, and removes only it."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from sketch2cad.catalog import catalog_json, default_catalog
from sketch2cad.db.session import create_db_engine

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
REVISION = "0006_seed_catalog"
PREVIOUS = "0005_plans_editor"
CATALOG_ROWS = "SELECT version, is_active, layers, symbols FROM catalog ORDER BY version"


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
def config(migration_schema: str) -> Config:
    alembic_config = Config(str(ALEMBIC_INI))
    alembic_config.attributes["schema"] = migration_schema
    alembic_config.attributes["skip_logging_config"] = True
    return alembic_config


@pytest.fixture
def engine(database_url: str, migration_schema: str) -> Iterator[Engine]:
    schema_engine = create_db_engine(
        database_url, connect_args={"options": f"-csearch_path={migration_schema}"}
    )
    try:
        yield schema_engine
    finally:
        schema_engine.dispose()


def execute(engine: Engine, statement: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement))


def catalog_rows(engine: Engine) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(CATALOG_ROWS))]


def test_upgrade_seeds_default_1_as_the_active_catalog(config: Config, engine: Engine) -> None:
    command.upgrade(config, REVISION)

    expected = catalog_json(default_catalog())
    [(version, is_active, layers, symbols)] = catalog_rows(engine)
    assert (version, is_active) == ("default-1", True)
    assert layers == expected["layers"]
    assert symbols == expected["symbols"]
    assert (len(expected["layers"]), len(expected["symbols"])) == (10, 8)


def test_upgrade_is_idempotent(config: Config, engine: Engine) -> None:
    command.upgrade(config, REVISION)
    first = catalog_rows(engine)

    command.stamp(config, PREVIOUS)
    command.upgrade(config, REVISION)

    assert catalog_rows(engine) == first


def test_seed_stays_inactive_when_another_catalog_is_active(config: Config, engine: Engine) -> None:
    command.upgrade(config, PREVIOUS)
    execute(
        engine,
        "INSERT INTO catalog (version, is_active, layers, symbols) "
        "VALUES ('custom-1', true, '[]', '[]')",
    )

    command.upgrade(config, REVISION)

    assert [(row[0], row[1]) for row in catalog_rows(engine)] == [
        ("custom-1", True),
        ("default-1", False),
    ]


def test_downgrade_removes_only_the_seeded_catalog(config: Config, engine: Engine) -> None:
    command.upgrade(config, REVISION)
    execute(
        engine,
        "INSERT INTO catalog (version, is_active, layers, symbols) "
        "VALUES ('custom-2', false, '[]', '[]')",
    )

    command.downgrade(config, PREVIOUS)

    assert [(row[0], row[1]) for row in catalog_rows(engine)] == [("custom-2", False)]
