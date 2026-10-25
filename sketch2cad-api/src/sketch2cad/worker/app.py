"""Procrastinate app of the worker, backed by the PostgreSQL database in ``DATABASE_URL``.

Commands (run from ``sketch2cad-api/``, with ``DATABASE_URL`` set):

- ``python -m sketch2cad.worker.app apply-schema`` applies the Procrastinate schema;
  it is idempotent (does nothing when ``procrastinate_jobs`` already exists).
- ``procrastinate --app=sketch2cad.worker.app.app worker`` starts the worker.

``DATABASE_URL`` is the SQLAlchemy URL used by the API (``postgresql+psycopg://...``);
it is converted to a libpq conninfo, and its query parameters (for example
``options=-csearch_path=...``) are forwarded as connection parameters. Only
``DATABASE_URL`` is read; the full application settings are not required.

Importing this module has no side effects: the module-level ``app`` that the
Procrastinate CLI loads is built on first access. No tasks are registered yet.
"""

import argparse
import os
import sys
from collections.abc import Sequence
from functools import lru_cache

import procrastinate
import psycopg
from procrastinate.schema import SchemaManager
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

EXIT_OK = 0
EXIT_ERROR = 1
SCHEMA_LOCK_KEY = 0x5C2CAD01
"""Advisory lock key that serialises concurrent schema applications."""


class WorkerConfigError(Exception):
    """The worker database configuration is missing or invalid."""


def to_conninfo(database_url: str, **overrides: str | int) -> str:
    """Convert a SQLAlchemy PostgreSQL URL into a libpq conninfo string.

    URL query parameters become connection parameters; ``overrides`` take precedence.
    Error messages never include the URL, since it carries credentials.
    """
    try:
        url = make_url(database_url)
    except ArgumentError:
        raise WorkerConfigError("DATABASE_URL is not a valid database URL") from None
    if url.get_backend_name() != "postgresql":
        raise WorkerConfigError("DATABASE_URL must point to a PostgreSQL database")
    params: dict[str, str | int | None] = {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "dbname": url.database,
    }
    for key, value in url.query.items():
        params[key] = value if isinstance(value, str) else value[-1]
    params.update(overrides)
    return make_conninfo("", **{key: value for key, value in params.items() if value is not None})


def conninfo_from_environment(**overrides: str | int) -> str:
    """Return the conninfo built from ``DATABASE_URL``; raise if it is not set."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise WorkerConfigError("DATABASE_URL is not set")
    return to_conninfo(database_url, **overrides)


def create_worker_app(conninfo: str | None = None) -> procrastinate.App:
    """Build the Procrastinate app; ``conninfo`` defaults to the one from ``DATABASE_URL``.

    No connection is opened here: the app connects when it is opened.
    """
    if conninfo is None:
        conninfo = conninfo_from_environment()
    return procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=conninfo))


def apply_schema(conninfo: str) -> bool:
    """Apply the Procrastinate schema unless it is already present.

    Runs in one transaction under an advisory lock, so concurrent or repeated calls are
    safe. The check honours the connection ``search_path``. Returns ``True`` when the
    schema was applied and ``False`` when it already existed.
    """
    schema_sql = SchemaManager.get_schema()
    with psycopg.connect(conninfo) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        row = connection.execute("SELECT to_regclass('procrastinate_jobs')").fetchone()
        if row is not None and row[0] is not None:
            return False
        # Procrastinate's own schema SQL; executed without parameters, as one script.
        connection.execute(schema_sql)
    return True


@lru_cache
def _module_app() -> procrastinate.App:
    return create_worker_app()


def __getattr__(name: str) -> procrastinate.App:
    """Build the module-level ``app`` lazily, for ``procrastinate --app=...app``."""
    if name == "app":
        return _module_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run(argv: Sequence[str]) -> int:
    """Run the module command line and return its exit status."""
    parser = argparse.ArgumentParser(prog="python -m sketch2cad.worker.app")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("apply-schema", help="apply the Procrastinate schema (idempotent)")
    args = parser.parse_args(argv)
    if args.command != "apply-schema":  # pragma: no cover - argparse rejects others
        return EXIT_ERROR
    try:
        applied = apply_schema(conninfo_from_environment())
    except WorkerConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except psycopg.Error as exc:
        # Only the exception type: driver messages may contain connection details.
        print(f"error: database error ({type(exc).__name__})", file=sys.stderr)
        return EXIT_ERROR
    print("procrastinate schema applied" if applied else "procrastinate schema already present")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
