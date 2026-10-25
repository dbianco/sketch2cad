"""Alembic environment: URL from ``DATABASE_URL``, target metadata from the ORM models.

An optional target schema can be given either as ``-x schema=<name>`` on the command
line or as ``config.attributes["schema"]`` when Alembic is invoked programmatically.
When set, online connections use it as ``search_path`` so every object, including the
``alembic_version`` table, is created inside that schema. Offline mode (``--sql``)
ignores it.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

import sketch2cad.db.models  # noqa: F401  (registers the tables on Base.metadata)
from sketch2cad.db.base import Base
from sketch2cad.db.session import create_db_engine

config = context.config

if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Return ``DATABASE_URL`` or fail with an explicit message."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; Alembic needs it to reach the database")
    return url


def get_target_schema() -> str | None:
    """Return the schema requested via ``-x schema=`` or ``config.attributes``."""
    schema = context.get_x_argument(as_dictionary=True).get("schema")
    return schema or config.attributes.get("schema")


def configure_and_run(connection: Connection) -> None:
    """Configure the migration context on ``connection`` and run the migrations."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (``alembic upgrade head --sql``)."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against the database."""
    schema = get_target_schema()
    connect_args: dict[str, object] = {}
    if schema:
        connect_args["options"] = f"-csearch_path={schema}"
    engine = create_db_engine(get_database_url(), connect_args=connect_args)
    try:
        with engine.connect() as connection:
            configure_and_run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
