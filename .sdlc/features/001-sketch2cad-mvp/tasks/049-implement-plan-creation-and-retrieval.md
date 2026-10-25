---
id: 001-049
title: "Implement plan creation and retrieval"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-046, 001-032]
implements: [REQ-03, US-04]
files: ["sketch2cad-api/src/sketch2cad/plans/__init__.py", "sketch2cad-api/src/sketch2cad/plans/service.py", "sketch2cad-api/src/sketch2cad/api/routers/plans.py", "sketch2cad-api/tests/plans/test_plans.py"]
parallelizable: false
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

`POST /case-files/{id}/plans` creates a plan in `CALIBRATION_PENDING` with `source_mode` default `schematic_sketch`, creates revision 1 in `draft`, writes `plan.created`, and enqueues no AI job. `GET /plans/{id}` returns calibration, lease and current revision. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-03, US-04

## Acceptance Criteria

- [ ] Creating a plan without `source_mode` stores `schematic_sketch` (`tests/plans/test_plans.py`).
- [ ] After creation the job table has zero rows with kind `suggest_lines`.
- [ ] A plan whose `primary_source_id` belongs to another case returns 422.
