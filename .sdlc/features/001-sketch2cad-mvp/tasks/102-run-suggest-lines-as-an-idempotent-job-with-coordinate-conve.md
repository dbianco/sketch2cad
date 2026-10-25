---
id: 001-102
title: "Run suggest_lines as an idempotent job with coordinate conversion"
status: todo
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-100, 001-051]
implements: [REQ-17, REQ-15]
files: ["sketch2cad-api/src/sketch2cad/worker/tasks_suggest.py", "sketch2cad-api/src/sketch2cad/vision/convert.py", "sketch2cad-api/src/sketch2cad/calibration/service.py", "sketch2cad-api/tests/vision/test_suggest_job.py"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 18 }
labels: [ai-sdlc, python]
---

## Description

Enqueue on calibration acceptance or `non_metric` selection with an idempotency key `(plan_id, calibration_id)`; rectified px → paper mm (÷10) → metres (`mm × paper_scale / 1000` in `scaled_drawing`; provisional 1:100 in `schematic_sketch`). Insert entities as `suggested`, `source.method = cv`, `metric_origin = trace_only` in one transaction with `suggestions.added`. Failures end as `failed` with cause and action. Relaunch allowed only while no entity is accepted. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-17, REQ-15

## Acceptance Criteria

- [ ] Accepting calibration twice creates one job; creating the plan creates none (H5-9, `tests/vision/test_suggest_job.py`).
- [ ] All inserted entities have `review_status = suggested`, `metric_origin = trace_only`, `source.method = cv` and a `suggestions.added` event with the count.
- [ ] Relaunching after one entity is accepted returns 409.
