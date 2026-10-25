---
id: 001-013
title: "Add the Procrastinate worker skeleton"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-003]
implements: [REQ-16]
files: ["sketch2cad-api/src/sketch2cad/worker/__init__.py", "sketch2cad-api/src/sketch2cad/worker/app.py", "sketch2cad-api/src/sketch2cad/worker/health.py", "sketch2cad-api/tests/worker/test_worker_boot.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 4, pessimistic: 7 }
labels: [ai-sdlc, python]
---

## Description

Procrastinate `App` over the PostgreSQL connector with the schema applied through its migration command in the compose startup. `health.py` exposes a check used by the compose healthcheck (exit 0 when the connector can open a connection). No tasks yet. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-16

## Acceptance Criteria

- [ ] `uv run pytest tests/worker/test_worker_boot.py -q` passes: the app opens a connection and `procrastinate schema` objects exist.
- [ ] `python -m sketch2cad.worker.health` exits 0 with the database up and non-zero with it stopped.
