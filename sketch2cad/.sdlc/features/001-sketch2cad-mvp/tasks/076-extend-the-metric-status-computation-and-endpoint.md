---
id: 001-076
title: "Extend the metric status computation and endpoint"
status: in_review
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-073, 001-075, 001-057]
implements: [REQ-07, US-11]
files: ["sketch2cad-api/src/sketch2cad/geometry/metric_status.py", "sketch2cad-api/src/sketch2cad/api/routers/plans.py", "sketch2cad-api/tests/geometry/test_metric_status.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

Apply the Spec §11.3 rule to `L_STRUCTURE`, `L_ACCESS` and entities referencing evidence. `GET /plans/{id}/metric-status` returns `{status, blocking_entities[]}`; the plan's `metric_status` updates on every mutation. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-07, US-11

## Acceptance Criteria

- [ ] A plan with a `trace_only` entity in `L_STRUCTURE` is `approximate`; replacing it with `declared_dimension` makes it `metric`; without calibration it is `non_metric` (H3-8, `tests/geometry/test_metric_status.py`).
- [ ] `blocking_entities` lists exactly the offending entity ids.
- [ ] `rg -n 'metric_status\s*=' src/ | rg -v 'metric_status.py|models/'` returns no direct assignments outside the module.
