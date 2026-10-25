---
id: 001-113
title: "Label CI tests by acceptance criterion and gate on them"
status: todo
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-111, 001-112]
implements: [REQ-16, REQ-15]
files: [".github/workflows/acceptance.yml", "sketch2cad-api/tests/conftest.py", "sketch2cad-api/scripts/ca_report.py", "sketch2cad-api/pyproject.toml"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 9 }
labels: [ai-sdlc, python]
---

## Description

Pytest markers `ca01` to `ca13` on the tests that realize each criterion; `ca_report.py` prints which criteria pass. CA-04 and CA-05 are required only when `features.h5_built` is true in `acceptance.toml`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-16, REQ-15

## Acceptance Criteria

- [ ] `uv run python scripts/ca_report.py` prints a table with CA-01 to CA-13 and pass or fail for each (H6-1).
- [ ] The workflow fails if any required criterion has zero tests: `uv run pytest --collect-only -m ca03 -q` lists at least one test for every `caNN` (checked by the script).
