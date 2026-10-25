---
id: 001-030
title: "Implement the hash-chained event writer"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-028]
implements: [REQ-11]
files: ["sketch2cad-api/src/sketch2cad/audit/__init__.py", "sketch2cad-api/src/sketch2cad/audit/chain.py", "sketch2cad-api/src/sketch2cad/audit/canonical.py", "sketch2cad-api/tests/audit/test_chain.py"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, python]
---

## Description

`append_event(session, ...)` locks the case_file row (`SELECT ... FOR UPDATE`), reads the last hash (first event uses `SHA-256(case_file_id)`), computes `hash = SHA-256(prev_hash || JCS(event without hash))` with the `rfc8785` package and inserts in the caller's transaction. The kinds list is a constant enum from Spec §14.1. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-11

## Acceptance Criteria

- [ ] Appending 3 events yields `events[i].prev_hash == events[i-1].hash` and the first `prev_hash == sha256(case_file_id)` (`tests/audit/test_chain.py`).
- [ ] Two threads appending 100 events each to the same case produce 200 events with no gap and no fork (H1-8), asserted by walking the chain.
- [ ] A rolled-back transaction leaves no event row.
