---
id: 001-039
title: "Add logging that excludes payloads and test it"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-034]
implements: [REQ-11]
files: ["sketch2cad-api/src/sketch2cad/logging.py", "sketch2cad-api/tests/test_log_hygiene.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 4, pessimistic: 6 }
labels: [ai-sdlc, python]
---

## Description

Structured logging that emits only identifiers (case, source, job ids). A test uploads a synthetic image named with a unique marker and captures all logs. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-11

## Acceptance Criteria

- [ ] `tests/test_log_hygiene.py` asserts the captured logs contain neither the file name marker, nor coordinates, nor any 16-byte slice of the image (H1-9).
- [ ] `rg -n 'logger\.(info|debug|warning).*(filename|geometry|coords)' src/` returns no matches.
