"""Database engine and session management (psycopg 3 driver)."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.config import get_settings


def create_db_engine(database_url: str, connect_args: dict[str, object] | None = None) -> Engine:
    """Create an engine for ``database_url`` (expected ``postgresql+psycopg://...``).

    ``connect_args`` is forwarded to the psycopg driver (for example ``options``).
    """
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args or {})


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@lru_cache
def get_engine() -> Engine:
    """Return the process-wide engine built from ``DATABASE_URL``."""
    return create_db_engine(get_settings().DATABASE_URL)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory bound to ``get_engine()``."""
    return create_session_factory(get_engine())


def get_session() -> Generator[Session]:
    """Yield a session and close it afterwards; usable as a FastAPI dependency."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
