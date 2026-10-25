---
id: 001-028
title: "Add the sources, events and job_meta migration with the immutable event trigger"
status: in_review
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-027]
implements: [REQ-02, REQ-11]
files: ["sketch2cad-api/migrations/versions/0002_sources_events.py", "sketch2cad-api/src/sketch2cad/db/models/source.py", "sketch2cad-api/src/sketch2cad/db/models/event.py", "sketch2cad-api/src/sketch2cad/db/models/job_meta.py", "sketch2cad-api/tests/test_models_events_schema.py"]
parallelizable: true
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

Tables `source`, `event` and `job_meta` as in Jot Down Milestone 2, section 3. A trigger rejects `UPDATE` and `DELETE` on `event`. Unique index `(case_file_id, id)` on events. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-02, REQ-11

## Acceptance Criteria

- [ ] `uv run alembic upgrade head` creates the three tables; `alembic downgrade -1` removes them (`tests/test_models_events_schema.py`).
- [ ] `UPDATE event SET reason = 'x'` and `DELETE FROM event` each fail with a database error (H1-7), asserted in the test.
- [ ] `scripts/migration_roundtrip.sh` exits 0.
