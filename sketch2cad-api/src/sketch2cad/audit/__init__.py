"""Append-only audit log: hash-chained events per case file (spec section 14.1)."""

from sketch2cad.audit.canonical import canonical_bytes, event_content
from sketch2cad.audit.chain import (
    EventKind,
    UnknownCaseFileError,
    append_event,
    compute_hash,
    genesis_hash,
)

__all__ = [
    "EventKind",
    "UnknownCaseFileError",
    "append_event",
    "canonical_bytes",
    "compute_hash",
    "event_content",
    "genesis_hash",
]
