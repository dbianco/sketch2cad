---
id: 001-031
title: "Implement chain verification"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-030]
implements: [REQ-11]
files: ["sketch2cad-api/src/sketch2cad/audit/verify.py", "sketch2cad-api/tests/audit/test_verify.py"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 8 }
labels: [ai-sdlc, python]
---

## Description

`verify_chain(events) -> VerifyResult` returning the id of the first invalid event and the reason. Must work on plain event dictionaries so the standalone verifier in H4 reuses it. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-11

## Acceptance Criteria

- [ ] Changing one byte in `after` of event 5 of 10 makes `verify_chain` return `first_invalid_id == events[4].id` (H1-6).
- [ ] A hypothesis property test mutates a random field of a random event and always yields an invalid result (`tests/audit/test_verify.py`).
- [ ] An unmodified chain of 1000 events verifies in under 1 second on the development machine.
