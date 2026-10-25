---
id: 001-016
title: "Export openapi.json and fail CI on contract drift"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-012]
implements: [REQ-16]
files: ["sketch2cad-api/scripts/export_openapi.py", "sketch2cad-api/openapi.json", "sketch2cad-api/tests/test_openapi_snapshot.py"]
parallelizable: false
estimate: { optimistic: 1, likely: 2, pessimistic: 4 }
labels: [ai-sdlc, python]
---

## Description

Script writes the FastAPI schema to `openapi.json`. The test regenerates it in memory and compares it with the committed file so a route change without regenerating fails. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-16

## Acceptance Criteria

- [ ] `uv run python scripts/export_openapi.py` rewrites `openapi.json` deterministically (running it twice leaves `git diff --stat` empty).
- [ ] `uv run pytest tests/test_openapi_snapshot.py -q` passes, and fails after adding an unrelated route without regenerating (verified once manually and recorded in the PR).
