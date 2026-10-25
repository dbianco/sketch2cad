"""Chain verifier over plain event dictionaries: tampering, links, isolation, speed (H1-6)."""

import hashlib
import json
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.audit import EventKind, append_event, compute_hash, event_content, genesis_hash
from sketch2cad.audit.verify import EVENT_FIELDS, InvalidReason, VerifyResult, verify_chain
from sketch2cad.db.base import Base
from sketch2cad.db.models import CaseFile, Event, IncidentType, User, UserRole

VERIFY_PATH = Path(__file__).resolve().parents[2] / "src" / "sketch2cad" / "audit" / "verify.py"
OCCURRED_AT = datetime(2026, 10, 25, 12, 30, 15, 123456, tzinfo=UTC)
CHAIN_LENGTH = 10
PERF_CHAIN_LENGTH = 1000
PERF_THRESHOLD_SECONDS = 1.0
PERF_RUNS = 5

EventDict = dict[str, Any]


def build_chain(
    count: int,
    *,
    case_file_id: uuid.UUID | None = None,
    ids: Sequence[int] | None = None,
    prev_hash: str | None = None,
    auth: bool = False,
) -> list[EventDict]:
    """Chain written with the production writer's ``compute_hash``, exported as dicts.

    ``auth=True`` builds the global auth chain (``case_file_id`` ``None``).
    """
    case_file = None if auth else case_file_id or uuid.uuid4()
    expected_prev = prev_hash or genesis_hash(case_file)
    events: list[EventDict] = []
    for index, event_id in enumerate(ids or range(1, count + 1)):
        event = Event(
            id=event_id,
            case_file_id=case_file,
            plan_id=uuid.uuid4(),
            revision_id=None,
            actor_id=uuid.uuid4(),
            actor_role="technician",
            kind=EventKind.ENTITY_UPDATED.value,
            entity_id=uuid.uuid4(),
            before={"length_mm": 1000 + index},
            after={"length_mm": 1250 + index, "label": f"wall {index}", "ok": True},
            reason="declared dimension",
            client_ip="192.0.2.10",
            occurred_at=OCCURRED_AT + timedelta(seconds=index),
            prev_hash=expected_prev,
        )
        event.hash = compute_hash(event)
        events.append(event_content(event) | {"hash": event.hash})
        expected_prev = event.hash
    return events


def rehash(event: EventDict) -> None:
    """Recompute ``hash`` after a change, as a forger who knows the algorithm would."""
    content = {name: value for name, value in event.items() if name != "hash"}
    digest = hashlib.sha256(event["prev_hash"].encode("ascii") + rfc8785.dumps(content))
    event["hash"] = digest.hexdigest()


def assert_invalid_at(result: VerifyResult, events: list[EventDict], index: int) -> None:
    assert not result.valid
    assert result.first_invalid_index == index
    assert result.first_invalid_id == events[index]["id"]
    assert result.checked == index


def test_unmodified_chain_is_valid() -> None:
    result = verify_chain(build_chain(CHAIN_LENGTH))
    assert result == VerifyResult(valid=True, checked=CHAIN_LENGTH)


def test_empty_chain_is_valid() -> None:
    assert verify_chain([]) == VerifyResult(valid=True, checked=0)


def test_event_fields_match_the_hashed_content_of_the_writer() -> None:
    (event,) = build_chain(1)
    assert set(event) == EVENT_FIELDS


def test_one_byte_changed_in_after_of_event_5_of_10_reports_event_5() -> None:
    events = build_chain(CHAIN_LENGTH)
    raw = bytearray(rfc8785.dumps(events[4]["after"]))
    position = raw.index(b"wall 4") + len("wall ")
    raw[position] = ord("9")
    events[4]["after"] = json.loads(raw)

    result = verify_chain(events)

    assert_invalid_at(result, events, 4)
    assert result.reason is InvalidReason.HASH_MISMATCH


def test_rehashed_tampered_event_breaks_the_link_of_the_next_event() -> None:
    events = build_chain(CHAIN_LENGTH)
    events[4]["reason"] = "forged"
    rehash(events[4])

    result = verify_chain(events)

    assert_invalid_at(result, events, 5)
    assert result.reason is InvalidReason.PREV_HASH_MISMATCH


def test_removed_middle_event_is_reported_at_its_successor() -> None:
    events = build_chain(CHAIN_LENGTH)
    del events[3]
    assert_invalid_at(verify_chain(events), events, 3)


def test_swapped_events_are_reported_at_the_first_swapped_position() -> None:
    events = build_chain(CHAIN_LENGTH)
    events[2], events[3] = events[3], events[2]
    assert_invalid_at(verify_chain(events), events, 2)


def test_first_event_must_link_to_the_genesis_hash() -> None:
    events = build_chain(3, prev_hash="0" * 64)
    result = verify_chain(events)
    assert_invalid_at(result, events, 0)
    assert result.reason is InvalidReason.PREV_HASH_MISMATCH


def test_well_linked_event_with_a_non_increasing_id_is_rejected() -> None:
    events = build_chain(4, ids=[1, 2, 3, 3])
    result = verify_chain(events)
    assert_invalid_at(result, events, 3)
    assert result.reason is InvalidReason.ID_NOT_INCREASING


def test_event_of_another_case_file_is_rejected() -> None:
    events = build_chain(3)
    events[2]["case_file_id"] = str(uuid.uuid4())
    rehash(events[2])
    result = verify_chain(events)
    assert_invalid_at(result, events, 2)
    assert result.reason is InvalidReason.CASE_FILE_MISMATCH


AUTH_GENESIS = hashlib.sha256(b"auth").hexdigest()


def test_global_auth_chain_is_anchored_on_the_sha256_of_auth() -> None:
    events = build_chain(CHAIN_LENGTH, auth=True)
    assert events[0]["case_file_id"] is None
    assert events[0]["prev_hash"] == AUTH_GENESIS == genesis_hash(None)
    assert verify_chain(events) == VerifyResult(valid=True, checked=CHAIN_LENGTH)


def test_global_auth_chain_with_another_genesis_is_rejected() -> None:
    events = build_chain(3, auth=True, prev_hash=genesis_hash(uuid.uuid4()))
    result = verify_chain(events)
    assert_invalid_at(result, events, 0)
    assert result.reason is InvalidReason.PREV_HASH_MISMATCH


def test_tampered_global_auth_event_is_reported() -> None:
    events = build_chain(CHAIN_LENGTH, auth=True)
    events[6]["after"]["label"] = "forged"
    result = verify_chain(events)
    assert_invalid_at(result, events, 6)
    assert result.reason is InvalidReason.HASH_MISMATCH


@pytest.mark.parametrize(
    ("auth", "foreign_case_file_id"),
    [(True, str(uuid.uuid4())), (False, None)],
    ids=["case_event_in_auth_chain", "auth_event_in_case_chain"],
)
def test_chains_do_not_mix_case_and_global_events(
    auth: bool, foreign_case_file_id: str | None
) -> None:
    events = build_chain(3, auth=auth)
    events[1]["case_file_id"] = foreign_case_file_id
    rehash(events[1])
    result = verify_chain(events)
    assert_invalid_at(result, events, 1)
    assert result.reason is InvalidReason.CASE_FILE_MISMATCH


MISSING = object()


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [
        ("kind", MISSING, "missing fields: ['kind']"),
        ("extra", 1, "unexpected fields: ['extra']"),
        ("id", "2", "id is not an integer"),
        ("id", True, "id is not an integer"),
        ("prev_hash", None, "prev_hash is not a string"),
    ],
)
def test_malformed_event_is_rejected(field: str, value: object, detail: str) -> None:
    events = build_chain(3)
    if value is MISSING:
        del events[1][field]
    else:
        events[1][field] = value
    result = verify_chain(events)
    assert result.first_invalid_index == 1
    assert result.reason is InvalidReason.MALFORMED
    assert result.detail == detail


def test_content_that_jcs_cannot_represent_is_rejected() -> None:
    events = build_chain(3)
    events[1]["after"] = {"length_mm": float("nan")}
    result = verify_chain(events)
    assert_invalid_at(result, events, 1)
    assert result.reason is InvalidReason.NOT_CANONICALIZABLE


def test_malformed_first_event_is_reported_at_index_0() -> None:
    events = build_chain(2)
    events[0]["case_file_id"] = 7
    result = verify_chain(events)
    assert_invalid_at(result, events, 0)
    assert result.reason is InvalidReason.MALFORMED


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53) + 1, max_value=2**53 - 1)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=5), children, max_size=3)
    ),
    max_leaves=6,
)


def flip_one_character(value: str, position: int, replacement: str) -> str:
    position %= len(value)
    if value[position] == replacement:
        replacement = "a" if replacement != "a" else "b"
    return value[:position] + replacement + value[position + 1 :]


def jcs_or_none(value: Any) -> bytes | None:
    try:
        return rfc8785.dumps(value)
    except rfc8785.CanonicalizationError:
        return None


@settings(max_examples=300, deadline=None)
@given(
    index=st.integers(min_value=0, max_value=CHAIN_LENGTH - 1),
    field=st.sampled_from(sorted(EVENT_FIELDS)),
    flip=st.booleans(),
    position=st.integers(min_value=0, max_value=1000),
    replacement=st.characters(codec="utf-8"),
    new_value=json_values,
)
def test_mutating_any_field_of_any_event_invalidates_the_chain_at_that_event(
    index: int,
    field: str,
    flip: bool,
    position: int,
    replacement: str,
    new_value: Any,
) -> None:
    events = build_chain(CHAIN_LENGTH)
    old_value = events[index][field]
    if flip and isinstance(old_value, str) and old_value:
        new_value = flip_one_character(old_value, position, replacement)
    if jcs_or_none(new_value) == jcs_or_none(old_value):
        new_value = [old_value]
    events[index][field] = new_value

    result = verify_chain(events)

    assert not result.valid
    assert result.first_invalid_index == index
    assert result.first_invalid_id == events[index]["id"]


@pytest.mark.perf
def test_unmodified_chain_of_1000_events_verifies_in_under_1_second() -> None:
    events = build_chain(PERF_CHAIN_LENGTH)
    timings: list[float] = []
    for _ in range(PERF_RUNS):
        start = time.perf_counter()
        result = verify_chain(events)
        timings.append(time.perf_counter() - start)
        assert result.valid
        assert result.checked == PERF_CHAIN_LENGTH
    assert min(timings) < PERF_THRESHOLD_SECONDS, f"best of {PERF_RUNS} runs: {min(timings):.3f}s"


def test_verifier_loads_without_sqlalchemy_or_the_application() -> None:
    script = (
        "import importlib.util, sys\n"
        "sys.modules['sqlalchemy'] = None\n"
        "sys.modules['sketch2cad'] = None\n"
        f"spec = importlib.util.spec_from_file_location('verify', {str(VERIFY_PATH)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "print(module.verify_chain([]).valid)\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and inline script
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "True"


@pytest.fixture(scope="module")
def tables(db_engine: Engine) -> Iterator[None]:
    Base.metadata.create_all(db_engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.fixture
def factory(tables: None, db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, expire_on_commit=False)


def test_chain_written_to_postgresql_and_read_back_verifies(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as session:
        user = User(
            username=f"tech-{uuid.uuid4().hex[:8]}",
            password_hash="not-a-real-hash",  # noqa: S106 - test fixture
            role=UserRole.TECHNICIAN,
        )
        session.add(user)
        session.flush()
        case_file = CaseFile(
            code=f"EXP-2026-{uuid.uuid4().hex[:8]}",
            incident_type=IncidentType.INSPECTION,
            incident_date=OCCURRED_AT.date(),
            description="test",
            declared_location="test",
            created_by=user.id,
        )
        session.add(case_file)
        session.flush()
        case_file_id = case_file.id
        for step in range(5):
            append_event(
                session,
                case_file_id=case_file_id,
                kind=EventKind.ENTITY_UPDATED,
                actor_id=user.id,
                actor_role="technician",
                after={"z": step, "a": [1.5, "ñandú", None, True], "nested": {"b": 2}},
                occurred_at=OCCURRED_AT,
            )

    with factory() as session:
        statement = select(Event).where(Event.case_file_id == case_file_id).order_by(Event.id)
        rows = list(session.scalars(statement).all())
    events: list[EventDict] = [event_content(row) | {"hash": row.hash} for row in rows]

    assert len(events) == 5
    assert verify_chain(events) == VerifyResult(valid=True, checked=5)
    events[2]["after"]["z"] = 99
    assert_invalid_at(verify_chain(events), events, 2)
