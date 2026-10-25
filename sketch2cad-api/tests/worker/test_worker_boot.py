"""Worker boot: Procrastinate app, idempotent schema application and health check."""

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import make_url

from sketch2cad.worker.app import (
    WorkerConfigError,
    apply_schema,
    create_worker_app,
    to_conninfo,
)

API_ROOT = Path(__file__).resolve().parents[2]
PROCRASTINATE_TABLES = {
    "procrastinate_events",
    "procrastinate_jobs",
    "procrastinate_periodic_defers",
    "procrastinate_workers",
}
UNREACHABLE_URL = "postgresql+psycopg://leaky_user:leaky_password@127.0.0.1:1/sketch2cad_test"


def schema_tables(conninfo: str, schema: str) -> set[str]:
    with psycopg.connect(conninfo) as connection:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (schema,),
        ).fetchall()
    return {str(row[0]) for row in rows}


@pytest.fixture
def worker_schema(database_url: str) -> Iterator[str]:
    """An empty schema for one test; dropped afterwards, verifying nothing remains."""
    schema = f"test_worker_{uuid.uuid4().hex}"
    admin_conninfo = to_conninfo(database_url)
    with psycopg.connect(admin_conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield schema
    finally:
        with psycopg.connect(admin_conninfo, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )
        assert schema_tables(admin_conninfo, schema) == set()


@pytest.fixture
def schema_conninfo(database_url: str, worker_schema: str) -> str:
    """A conninfo whose ``search_path`` is the isolated worker schema."""
    return to_conninfo(database_url, options=f"-csearch_path={worker_schema}")


def url_with_schema(database_url: str, schema: str) -> str:
    """The SQLAlchemy URL with ``search_path`` set to ``schema`` in its query string."""
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    return url.render_as_string(hide_password=False)


def run_command(args: list[str], database_url: str) -> subprocess.CompletedProcess[str]:
    """Run a command with only ``DATABASE_URL`` configured among the app settings."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"SESSION_SECRET", "SIGNING_KEY_PATH", "APP_ENV"}
    }
    env["DATABASE_URL"] = database_url
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        args, capture_output=True, text=True, cwd=API_ROOT, env=env, timeout=60, check=False
    )


def test_to_conninfo_converts_sqlalchemy_url_and_forwards_query_parameters() -> None:
    conninfo = to_conninfo(
        "postgresql+psycopg://user:pw@db.example:5555/name?options=-csearch_path%3Dx",
        connect_timeout=3,
    )
    params = conninfo_to_dict(conninfo)
    assert params == {
        "host": "db.example",
        "port": "5555",
        "user": "user",
        "password": "pw",
        "dbname": "name",
        "options": "-csearch_path=x",
        "connect_timeout": "3",
    }


@pytest.mark.parametrize(
    "url", ["not a url at all secret", "sqlite:///secret.db", "mysql://u:secret@h/d"]
)
def test_to_conninfo_rejects_invalid_urls_without_echoing_them(url: str) -> None:
    with pytest.raises(WorkerConfigError) as excinfo:
        to_conninfo(url)
    assert "secret" not in str(excinfo.value)


def test_app_opens_a_connection_and_finds_the_applied_schema(
    schema_conninfo: str, worker_schema: str
) -> None:
    with create_worker_app(schema_conninfo).open() as app:
        assert app.check_connection() is False
        assert apply_schema(schema_conninfo) is True
        assert app.check_connection() is True
    assert PROCRASTINATE_TABLES <= schema_tables(schema_conninfo, worker_schema)


def test_applying_the_schema_twice_does_not_fail(schema_conninfo: str, worker_schema: str) -> None:
    assert apply_schema(schema_conninfo) is True
    tables = schema_tables(schema_conninfo, worker_schema)
    assert apply_schema(schema_conninfo) is False
    assert schema_tables(schema_conninfo, worker_schema) == tables


def test_apply_schema_command_is_idempotent(database_url: str, worker_schema: str) -> None:
    url = url_with_schema(database_url, worker_schema)
    command = [sys.executable, "-m", "sketch2cad.worker.app", "apply-schema"]
    first = run_command(command, url)
    second = run_command(command, url)
    assert (first.returncode, second.returncode) == (0, 0), first.stderr + second.stderr
    assert "procrastinate schema applied" in first.stdout
    assert "procrastinate schema already present" in second.stdout


def test_procrastinate_cli_loads_the_module_level_app(
    database_url: str, worker_schema: str, schema_conninfo: str
) -> None:
    apply_schema(schema_conninfo)
    procrastinate_bin = Path(sys.executable).with_name("procrastinate")
    result = run_command(
        [str(procrastinate_bin), "--app=sketch2cad.worker.app.app", "healthchecks"],
        url_with_schema(database_url, worker_schema),
    )
    assert result.returncode == 0, result.stderr
    assert "Found procrastinate_jobs table: OK" in result.stdout


def test_importing_the_app_module_has_no_side_effects() -> None:
    # No DATABASE_URL: the module must import because ``app`` is only built on access.
    result = run_command([sys.executable, "-c", "import sketch2cad.worker.app"], "")
    assert result.returncode == 0, result.stderr


def test_health_exits_zero_with_the_database_up(database_url: str) -> None:
    result = run_command([sys.executable, "-m", "sketch2cad.worker.health"], database_url)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("healthy")


def test_health_fails_without_leaking_credentials_when_unreachable() -> None:
    result = run_command([sys.executable, "-m", "sketch2cad.worker.health"], UNREACHABLE_URL)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "unhealthy: database unreachable" in output
    for secret in ("leaky_user", "leaky_password", "127.0.0.1", UNREACHABLE_URL):
        assert secret not in output


def test_health_fails_when_database_url_is_missing() -> None:
    result = run_command([sys.executable, "-m", "sketch2cad.worker.health"], "")
    assert result.returncode == 1
    assert "DATABASE_URL is not set" in result.stderr
