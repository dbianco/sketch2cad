---
id: 001-038
title: "Run QC as a worker job and expose job status"
status: in_review
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-037, 001-036]
implements: [REQ-02, REQ-15]
files: ["sketch2cad-api/src/sketch2cad/worker/tasks_qc.py", "sketch2cad-api/src/sketch2cad/jobs/__init__.py", "sketch2cad-api/src/sketch2cad/jobs/service.py", "sketch2cad-api/src/sketch2cad/api/routers/jobs.py", "sketch2cad-api/tests/jobs/test_qc_job.py"]
parallelizable: false
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

Task `qc` loads the source, writes `source.qc` and `job_meta`; `GET /api/v1/jobs/{id}` returns `status` (queued, running, succeeded, failed) and, on failure, `error {cause, action}` (REQ-15). Unreadable images produce `failed` with a Spanish cause and action, never a default green. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-02, REQ-15

## Acceptance Criteria

- [ ] After ingesting a sharp image and running the worker once, `GET /jobs/{id}` returns `succeeded` and `source.qc.status == "green"` (`tests/jobs/test_qc_job.py`).
- [ ] A corrupt JPEG yields job `failed` with non-empty `error.cause` and `error.action`, and `source.qc` is not green.
- [ ] A job for a source in another case returns 403 to a non-member and writes `access.denied` (corrected 2026-10-25: non-members get 403, not 404, as everywhere else; see `api/deps.py`).
