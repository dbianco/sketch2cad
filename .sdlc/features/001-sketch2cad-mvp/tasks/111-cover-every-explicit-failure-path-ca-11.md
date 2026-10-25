---
id: 001-111
title: "Cover every explicit failure path (CA-11)"
status: todo
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-110, 001-098]
implements: [REQ-15, US-18]
files: ["sketch2cad-api/tests/failures/test_explicit_failures.py", "sketch2cad-api/src/sketch2cad/api/errors.py"]
parallelizable: true
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

Integration tests for unreadable image, calibration out of tolerance and forced job failure; each must yield a state with non-empty `cause` and `action` and never a green default. Fix any path that does not. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-15, US-18

## Acceptance Criteria

- [ ] `uv run pytest tests/failures/test_explicit_failures.py -q` passes: a Laplacian-variance-30 image, a 1.3 mm calibration and a job forced to fail each return `cause` and `action` non-empty (H6-2).
- [ ] `rg -n 'except Exception:\s*$' -A1 src/ | rg 'pass'` returns no silent exception swallowing.
