"""Worker health check: ``python -m sketch2cad.worker.health``.

Opens a connection through the Procrastinate connector built from ``DATABASE_URL`` and
runs a trivial query. Exit status 0 when the database answers, 1 otherwise (missing or
invalid ``DATABASE_URL``, unreachable database). Messages never include the URL or the
credentials. Used by the compose ``healthcheck`` of the ``worker`` service.
"""

import asyncio
import logging
import sys

import psycopg
import psycopg_pool

from sketch2cad.worker.app import (
    EXIT_ERROR,
    EXIT_OK,
    WorkerConfigError,
    conninfo_from_environment,
    create_worker_app,
)

CONNECT_TIMEOUT_SECONDS = 3


async def check_database(conninfo: str, timeout: float = CONNECT_TIMEOUT_SECONDS) -> bool:
    """Open the worker app on a one-connection pool and run a trivial query.

    Returns whether the Procrastinate schema is present; raises when the database
    cannot be reached within ``timeout`` seconds.
    """
    pool = psycopg_pool.AsyncConnectionPool(conninfo, min_size=1, max_size=1, open=False)
    try:
        await pool.open(wait=True, timeout=timeout)
        async with create_worker_app(conninfo).open_async(pool) as app:
            return await app.check_connection_async()
    finally:
        await pool.close()


def run() -> int:
    """Run the health check and return its exit status."""
    # The pool logs each failed attempt with host details; keep the output to our message.
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    try:
        conninfo = conninfo_from_environment(connect_timeout=CONNECT_TIMEOUT_SECONDS)
        schema_present = asyncio.run(check_database(conninfo))
    except WorkerConfigError as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except psycopg.Error as exc:
        # Only the exception type: driver messages may contain connection details.
        print(f"unhealthy: database unreachable ({type(exc).__name__})", file=sys.stderr)
        return EXIT_ERROR
    print("healthy" if schema_present else "healthy (procrastinate schema not applied yet)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(run())
