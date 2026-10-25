---
id: 001-120
title: "Scan test logs for coordinates and sketch file names"
status: todo
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-113]
implements: [REQ-11]
files: ["sketch2cad-api/scripts/scan_logs.py", "sketch2cad-api/tests/test_scan_logs.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 3, pessimistic: 5 }
labels: [ai-sdlc, python]
---

## Description

A script scans the CI logs of a full run for geometry and file-name patterns used by the validation set. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-11

## Acceptance Criteria

- [ ] `uv run python scripts/scan_logs.py ci.log` exits 0 on the run logs and exits 1 on a log containing a known sketch file name (`tests/test_scan_logs.py`, H6-8).
