---
id: 001-006
title: "Add problem+json error handling and the health endpoint"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-003]
implements: [REQ-16, REQ-15]
files: ["sketch2cad-api/src/sketch2cad/api/__init__.py", "sketch2cad-api/src/sketch2cad/api/app.py", "sketch2cad-api/src/sketch2cad/api/errors.py", "sketch2cad-api/src/sketch2cad/api/routers/__init__.py", "sketch2cad-api/src/sketch2cad/api/routers/health.py", "sketch2cad-api/tests/api/test_health_errors.py"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 8 }
labels: [ai-sdlc, python]
---

## Description

Create the FastAPI app factory, a `ProblemError` exception with `type`, `title`, `status`, `detail` rendered as `application/problem+json` (RFC 9457) and handlers for validation errors. `GET /healthz` returns `{"status":"ok","db":"ok"}` and 503 when the database does not answer. Prefix all other routers with `/api/v1`. Jot Down: Milestone 1, section 4. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-16, REQ-15

## Acceptance Criteria

- [ ] `GET /healthz` returns 200 with the body above; with the database stopped it returns 503 (asserted in `tests/api/test_health_errors.py`).
- [ ] A request that fails validation returns status 422 with header `content-type: application/problem+json` and keys `type`, `title`, `status`, `detail`.
- [ ] `uv run pytest tests/api/test_health_errors.py -q` passes.
