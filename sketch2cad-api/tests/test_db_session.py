import re

from sqlalchemy import (
    CheckConstraint,
    Column,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.db import NAMING_CONVENTION, Base, get_session

CONSTRAINT_NAME_PATTERN = re.compile(r"^(pk|fk|uq|ix|ck)_")


def test_select_one_runs_through_get_session(
    db_session_factory: sessionmaker[Session], test_schema: str
) -> None:
    sessions = get_session()
    session = next(sessions)
    try:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        assert session.execute(text("SELECT current_schema()")).scalar_one() == test_schema
    finally:
        sessions.close()


def test_base_metadata_uses_naming_convention() -> None:
    assert dict(Base.metadata.naming_convention) == NAMING_CONVENTION


def test_constraint_names_follow_naming_convention(db_engine: Engine, test_schema: str) -> None:
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    Table(
        "parent",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
    )
    Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parent.id"), nullable=False, index=True),
        Column("quantity", Integer, nullable=False),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
    )
    metadata.create_all(db_engine)
    try:
        with db_engine.connect() as connection:
            constraint_names = set(
                connection.execute(
                    text(
                        "SELECT c.conname FROM pg_constraint c "
                        "JOIN pg_namespace n ON n.oid = c.connamespace "
                        "WHERE n.nspname = :schema AND c.contype IN ('p', 'f', 'u', 'c')"
                    ),
                    {"schema": test_schema},
                ).scalars()
            )
            index_names = set(
                connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = :schema"),
                    {"schema": test_schema},
                ).scalars()
            )
    finally:
        metadata.drop_all(db_engine)

    assert constraint_names == {
        "pk_parent",
        "uq_parent_code",
        "pk_child",
        "fk_child_parent_id_parent",
        "ck_child_quantity_non_negative",
    }
    assert "ix_child_parent_id" in index_names
    for name in constraint_names | index_names:
        assert CONSTRAINT_NAME_PATTERN.match(name), name
