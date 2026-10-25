"""Shared fixtures; PostgreSQL fixtures use an isolated schema per test session."""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.db import session as db_session
from sketch2cad.db.session import create_db_engine, create_session_factory

SKIP_REASON = (
    "DATABASE_URL is not set; PostgreSQL-backed tests require it, e.g. "
    "DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/<database>"
)


@pytest.fixture(scope="session")
def database_url() -> str:
    """Return ``DATABASE_URL`` or skip the dependent tests with a clear reason."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip(SKIP_REASON)
    return url


@pytest.fixture(scope="session")
def test_schema(database_url: str) -> Iterator[str]:
    """Create a uniquely named schema for this test session and drop it afterwards."""
    schema = f"test_{uuid.uuid4().hex}"
    admin_engine = create_db_engine(database_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield schema
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture(scope="session")
def db_engine(database_url: str, test_schema: str) -> Iterator[Engine]:
    """Engine whose connections use the isolated test schema as ``search_path``."""
    engine = create_db_engine(
        database_url, connect_args={"options": f"-csearch_path={test_schema}"}
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session_factory(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> sessionmaker[Session]:
    """Bind ``get_session`` to the test engine for the duration of one test."""
    factory = create_session_factory(db_engine)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    return factory
