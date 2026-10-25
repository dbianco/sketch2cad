"""Hash chain verifier over plain event dictionaries (spec section 14.1, REQ-11, H1-6).

Each event is a mapping with exactly the JSON-ready fields of
:func:`sketch2cad.audit.canonical.event_content` plus ``hash``: UUIDs as lowercase
hyphenated strings, ``occurred_at`` as UTC ISO 8601 text and ``before``/``after`` as JSON
values. The verifier rebuilds ``SHA-256(prev_hash || JCS(event without hash))`` itself and
walks the links from the genesis hash ``SHA-256(case_file_id)``.

This module depends only on the standard library and ``rfc8785``: it imports nothing from
SQLAlchemy or the rest of the application, so the standalone ``sketch2cad-verify`` (H4) can
load it from a package export without a database.

Verification stops at the first invalid event. Events are checked in the order given, which
must be the chain order (ascending ``id``); reordering, a gap or a removed event in the
middle is reported at the first event whose link no longer matches. Removing events from
the end of the chain cannot be detected from the events alone; the H4 manifest records
``events_chain.count`` and ``last_hash`` for that.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import rfc8785

CONTENT_FIELDS = (
    "id",
    "case_file_id",
    "plan_id",
    "revision_id",
    "actor_id",
    "actor_role",
    "kind",
    "entity_id",
    "before",
    "after",
    "reason",
    "client_ip",
    "occurred_at",
    "prev_hash",
)
EVENT_FIELDS = frozenset((*CONTENT_FIELDS, "hash"))


class InvalidReason(StrEnum):
    """Why an event breaks the chain."""

    MALFORMED = "malformed"
    CASE_FILE_MISMATCH = "case_file_mismatch"
    ID_NOT_INCREASING = "id_not_increasing"
    PREV_HASH_MISMATCH = "prev_hash_mismatch"
    NOT_CANONICALIZABLE = "not_canonicalizable"
    HASH_MISMATCH = "hash_mismatch"


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of :func:`verify_chain`.

    ``first_invalid_id`` is the ``id`` value of the first invalid event as found in the
    input (it may be the tampered value itself) and ``first_invalid_index`` its position.
    All three failure fields are ``None`` when the chain is valid.
    """

    valid: bool
    checked: int
    first_invalid_id: Any = None
    first_invalid_index: int | None = None
    reason: InvalidReason | None = None
    detail: str | None = None


class ChainError(Exception):
    """Internal signal: the current event is invalid."""

    def __init__(self, reason: InvalidReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def genesis_hash_of(case_file_id: str) -> str:
    """``prev_hash`` of the first event of the chain of ``case_file_id`` (a UUID string)."""
    return sha256_hex(case_file_id.encode("ascii"))


def recompute_hash(event: Mapping[str, Any]) -> str:
    """``SHA-256(prev_hash || JCS(content))`` of an event dictionary.

    Raises ``rfc8785.CanonicalizationError`` when the content is not representable in JCS
    and ``UnicodeEncodeError`` when ``prev_hash`` is not ASCII.
    """
    content = {name: event[name] for name in CONTENT_FIELDS}
    return sha256_hex(event["prev_hash"].encode("ascii") + rfc8785.dumps(content))


def check_shape(event: Mapping[str, Any]) -> None:
    """Exact field set and the types the link checks rely on."""
    fields = set(event.keys())
    missing = EVENT_FIELDS - fields
    if missing:
        raise ChainError(InvalidReason.MALFORMED, f"missing fields: {sorted(missing)}")
    unexpected = fields - EVENT_FIELDS
    if unexpected:
        raise ChainError(InvalidReason.MALFORMED, f"unexpected fields: {sorted(unexpected)}")
    event_id = event["id"]
    if not isinstance(event_id, int) or isinstance(event_id, bool):
        raise ChainError(InvalidReason.MALFORMED, "id is not an integer")
    for name in ("case_file_id", "prev_hash", "hash"):
        if not isinstance(event[name], str):
            raise ChainError(InvalidReason.MALFORMED, f"{name} is not a string")


def check_event(
    event: Mapping[str, Any], case_file_id: str, previous_id: int | None, expected_prev: str
) -> None:
    """Raise :class:`ChainError` when ``event`` does not continue the chain."""
    check_shape(event)
    if event["case_file_id"] != case_file_id:
        raise ChainError(InvalidReason.CASE_FILE_MISMATCH, "event belongs to another case file")
    if previous_id is not None and event["id"] <= previous_id:
        raise ChainError(InvalidReason.ID_NOT_INCREASING, "id does not follow the previous id")
    if event["prev_hash"] != expected_prev:
        raise ChainError(
            InvalidReason.PREV_HASH_MISMATCH, "prev_hash does not match the previous hash"
        )
    try:
        recomputed = recompute_hash(event)
    except (rfc8785.CanonicalizationError, UnicodeEncodeError) as exc:
        raise ChainError(InvalidReason.NOT_CANONICALIZABLE, str(exc)) from exc
    if recomputed != event["hash"]:
        raise ChainError(InvalidReason.HASH_MISMATCH, "stored hash does not match the content")


def verify_chain(events: Sequence[Mapping[str, Any]]) -> VerifyResult:
    """Verify the hash chain of one case file, given its events oldest first.

    The case file is the one named by the first event; its genesis hash anchors the chain.
    An empty sequence is a valid, empty chain.
    """
    case_file_id: str = ""
    previous_id: int | None = None
    expected_prev = ""
    for index, event in enumerate(events):
        try:
            if index == 0:
                check_shape(event)
                case_file_id = event["case_file_id"]
                try:
                    expected_prev = genesis_hash_of(case_file_id)
                except UnicodeEncodeError as exc:
                    raise ChainError(InvalidReason.MALFORMED, "case_file_id is not ASCII") from exc
            check_event(event, case_file_id, previous_id, expected_prev)
        except ChainError as error:
            return VerifyResult(
                valid=False,
                checked=index,
                first_invalid_id=event.get("id"),
                first_invalid_index=index,
                reason=error.reason,
                detail=error.detail,
            )
        previous_id = event["id"]
        expected_prev = event["hash"]
    return VerifyResult(valid=True, checked=len(events))
