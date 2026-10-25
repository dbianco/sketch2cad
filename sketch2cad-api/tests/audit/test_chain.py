"""Hash-chained event writer: linking, canonical content, concurrency and rollback (REQ-11)."""

import hashlib
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import rfc8785
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.audit import (
    EventKind,
    UnknownCaseFileError,
    append_event,
    canonical_bytes,
    compute_hash,
    event_content,
    genesis_hash,
)
from sketch2cad.db.models import CaseFile, Event, IncidentType, User, UserRole
from sketch2cad.db.session import create_db_engine

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
OCCURRED_AT = datetime(2026, 10, 25, 12, 30, 15, 123456, tzinfo=UTC)
EVENTS_PER_THREAD = 100
THREAD_TIMEOUT_SECONDS = 60
SPEC_KINDS = {
    "case_file.created",
    "member.added",
    "source.ingested",
    "source.downloaded",
    "plan.created",
    "calibration.proposed",
    "calibration.accepted",
    "local_frame.set",
    "local_frame.changed",
    "suggestions.added",
    "evidence.created",
    "evidence.updated",
    "entity.created",
    "entity.updated",
    "entity.recomputed",
    "entity.reviewed",
    "entity.deleted",
    "plan.transition",
    "revision.frozen",
    "export.requested",
    "artifact.created",
    "artifact.downloaded",
    "lease.acquired",
    "lease.released",
    "auth.login",
    "auth.failed",
    "access.denied",
    "config.changed",
}


@pytest.fixture(scope="module")
def migrated(database_url: str) -> Iterator[Engine]:
    """Engine on a fresh schema migrated to head, so the append-only trigger is present."""
    schema = f"test_audit_{uuid.uuid4().hex}"
    admin_engine = create_db_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_db_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        config = Config(str(ALEMBIC_INI))
        config.attributes["schema"] = schema
        config.attributes["skip_logging_config"] = True
        command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def factory(migrated: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated, expire_on_commit=False)


@pytest.fixture
def actor_id(factory: sessionmaker[Session]) -> uuid.UUID:
    with factory.begin() as session:
        user = User(
            username=f"tech-{uuid.uuid4().hex[:8]}",
            password_hash="not-a-real-hash",  # noqa: S106 - test fixture
            role=UserRole.TECHNICIAN,
        )
        session.add(user)
        session.flush()
        return user.id


@pytest.fixture
def case_file_id(factory: sessionmaker[Session], actor_id: uuid.UUID) -> uuid.UUID:
    with factory.begin() as session:
        case_file = CaseFile(
            code=f"EXP-2026-{uuid.uuid4().hex[:8]}",
            incident_type=IncidentType.INSPECTION,
            incident_date=OCCURRED_AT.date(),
            description="test",
            declared_location="test",
            created_by=actor_id,
        )
        session.add(case_file)
        session.flush()
        return case_file.id


def chain_of(factory: sessionmaker[Session], case_file_id: uuid.UUID) -> list[Event]:
    """Events of ``case_file_id`` read back from the database, oldest first."""
    with factory() as session:
        statement = select(Event).where(Event.case_file_id == case_file_id).order_by(Event.id)
        return list(session.scalars(statement).all())


def assert_valid_chain(events: list[Event], case_file_id: uuid.UUID) -> None:
    """Walk the chain: genesis link, each link to its predecessor, each hash recomputed."""
    expected_prev = genesis_hash(case_file_id)
    for event in events:
        assert event.prev_hash == expected_prev, f"broken link at event {event.id}"
        assert event.hash == compute_hash(event), f"hash mismatch at event {event.id}"
        expected_prev = event.hash


def test_three_events_link_to_each_other_and_to_the_genesis_hash(
    factory: sessionmaker[Session], case_file_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    with factory.begin() as session:
        for kind in (
            EventKind.CASE_FILE_CREATED,
            EventKind.MEMBER_ADDED,
            EventKind.SOURCE_INGESTED,
        ):
            append_event(
                session,
                case_file_id=case_file_id,
                kind=kind,
                actor_id=actor_id,
                actor_role="technician",
                after={"step": kind.value},
                occurred_at=OCCURRED_AT,
            )

    events = chain_of(factory, case_file_id)
    assert len(events) == 3
    assert events[0].prev_hash == hashlib.sha256(str(case_file_id).encode()).hexdigest()
    for i in range(1, 3):
        assert events[i].prev_hash == events[i - 1].hash
    assert [e.kind for e in events] == ["case_file.created", "member.added", "source.ingested"]
    assert_valid_chain(events, case_file_id)


def test_hash_read_back_from_the_database_matches_the_written_hash(
    factory: sessionmaker[Session], case_file_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    payload = {"z": 1, "a": [1.5, "ñandú", None, True], "nested": {"b": 2, "a": 1}}
    with factory.begin() as session:
        written = append_event(
            session,
            case_file_id=case_file_id,
            kind=EventKind.ENTITY_UPDATED,
            actor_id=actor_id,
            actor_role="technician",
            plan_id=uuid.uuid4(),
            revision_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            before={"operation": "fix_length", "length_mm": 1000},
            after=payload,
            reason="declared dimension",
            client_ip="192.0.2.10",
            occurred_at=OCCURRED_AT,
        )
        written_hash = written.hash

    (stored,) = chain_of(factory, case_file_id)
    assert stored.hash == written_hash
    assert compute_hash(stored) == written_hash
    expected = hashlib.sha256(stored.prev_hash.encode() + rfc8785.dumps(event_content(stored)))
    assert stored.hash == expected.hexdigest()


def test_hashed_content_covers_every_stored_column_except_hash(
    factory: sessionmaker[Session], case_file_id: uuid.UUID
) -> None:
    with factory.begin() as session:
        event = append_event(
            session,
            case_file_id=case_file_id,
            kind=EventKind.ACCESS_DENIED,
            occurred_at=OCCURRED_AT,
        )
        content = event_content(event)

    columns = {column.name for column in Event.__table__.columns}
    assert set(content) == columns - {"hash"}
    assert content["occurred_at"] == "2026-10-25T12:30:15.123456Z"
    assert content["id"] == event.id
    assert canonical_bytes(event).startswith(b'{"actor_id":null,"actor_role":null,"after":null')


def test_changing_any_hashed_field_changes_the_hash(
    factory: sessionmaker[Session], case_file_id: uuid.UUID
) -> None:
    with factory.begin() as session:
        event = append_event(
            session, case_file_id=case_file_id, kind=EventKind.AUTH_LOGIN, occurred_at=OCCURRED_AT
        )
        session.expunge(event)

    original = event.hash
    event.occurred_at = OCCURRED_AT.replace(microsecond=123457)
    assert compute_hash(event) != original
    event.occurred_at = OCCURRED_AT
    event.reason = "tampered"
    assert compute_hash(event) != original


def test_two_threads_appending_100_events_each_produce_one_chain_without_gaps_or_forks(
    factory: sessionmaker[Session], case_file_id: uuid.UUID
) -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(label: str) -> None:
        try:
            barrier.wait(timeout=THREAD_TIMEOUT_SECONDS)
            for n in range(EVENTS_PER_THREAD):
                with factory.begin() as session:
                    append_event(
                        session,
                        case_file_id=case_file_id,
                        kind=EventKind.ENTITY_CREATED,
                        actor_role=label,
                        after={"n": n},
                    )
        except BaseException as error:  # reported by the main thread
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=THREAD_TIMEOUT_SECONDS)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []

    events = chain_of(factory, case_file_id)
    assert len(events) == 2 * EVENTS_PER_THREAD
    assert len({e.prev_hash for e in events}) == len(events), "fork: shared prev_hash"
    assert len({e.hash for e in events}) == len(events)
    assert_valid_chain(events, case_file_id)
    for label in ("a", "b"):
        numbers = [e.after["n"] for e in events if e.actor_role == label and e.after]
        assert numbers == list(range(EVENTS_PER_THREAD))


def test_rolled_back_transaction_leaves_no_event_row(
    factory: sessionmaker[Session], case_file_id: uuid.UUID
) -> None:
    with factory() as session:
        append_event(session, case_file_id=case_file_id, kind=EventKind.SOURCE_INGESTED)
        session.rollback()

    with factory() as session:
        count = session.scalar(
            select(func.count()).select_from(Event).where(Event.case_file_id == case_file_id)
        )
    assert count == 0

    with factory.begin() as session:
        first = append_event(session, case_file_id=case_file_id, kind=EventKind.SOURCE_INGESTED)
    assert first.prev_hash == genesis_hash(case_file_id)


def test_append_holds_the_case_file_lock_until_the_transaction_ends(
    factory: sessionmaker[Session], case_file_id: uuid.UUID
) -> None:
    with factory() as holder, factory() as waiter:
        append_event(holder, case_file_id=case_file_id, kind=EventKind.LEASE_ACQUIRED)
        waiter.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            append_event(waiter, case_file_id=case_file_id, kind=EventKind.LEASE_ACQUIRED)
        waiter.rollback()
        holder.commit()


def test_unknown_case_file_raises_and_inserts_nothing(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        with pytest.raises(UnknownCaseFileError):
            append_event(session, case_file_id=uuid.uuid4(), kind=EventKind.ACCESS_DENIED)
        assert not session.new


def test_naive_occurred_at_is_rejected(
    factory: sessionmaker[Session], case_file_id: uuid.UUID
) -> None:
    with factory() as session, pytest.raises(ValueError, match="timezone-aware"):
        append_event(
            session,
            case_file_id=case_file_id,
            kind=EventKind.CONFIG_CHANGED,
            occurred_at=datetime(2026, 10, 25, 12, 0),  # naive on purpose
        )


def test_payload_that_jcs_cannot_represent_is_rejected(
    factory: sessionmaker[Session], case_file_id: uuid.UUID
) -> None:
    with factory() as session, pytest.raises(rfc8785.CanonicalizationError):
        append_event(
            session, case_file_id=case_file_id, kind=EventKind.CONFIG_CHANGED, after={"n": 2**60}
        )


def test_event_kinds_match_spec_section_14_1() -> None:
    assert {kind.value for kind in EventKind} == SPEC_KINDS
    assert len(EventKind) == 28
