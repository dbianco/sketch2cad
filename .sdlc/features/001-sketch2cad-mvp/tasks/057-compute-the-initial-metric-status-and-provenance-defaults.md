---
id: 001-057
title: "Compute the initial metric status and provenance defaults"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-052, 001-050]
implements: [REQ-07, US-11]
files: ["sketch2cad-api/src/sketch2cad/geometry/metric_status.py", "sketch2cad-api/src/sketch2cad/plans/service.py", "sketch2cad-api/tests/geometry/test_metric_status_basic.py"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 9 }
labels: [ai-sdlc, python]
---

## Description

Initial rule set (H3 extends it): plan without accepted calibration is `non_metric`; in `scaled_drawing` with metric calibration, hand-drawn entities get `metric_origin = scaled_drawing`; in `schematic_sketch`, hand-drawn entities without a dimension get `none`. The function returns `{status, blocking_entities[]}`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-07, US-11

## Acceptance Criteria

- [ ] `non_metric` is returned for a plan with calibration method `none` (`tests/geometry/test_metric_status_basic.py`).
- [ ] In `scaled_drawing` with metric calibration, a drawn line gets `scaled_drawing` and the status is `metric`.
- [ ] In `schematic_sketch`, a drawn line gets `none` and the status is `approximate`, listing the entity in `blocking_entities`.
