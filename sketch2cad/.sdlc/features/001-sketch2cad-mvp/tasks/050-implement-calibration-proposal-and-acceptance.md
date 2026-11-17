---
id: 001-050
title: "Implement calibration proposal and acceptance"
status: in_review
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-049, 001-048]
implements: [REQ-03, US-03, V-05, V-08]
files: ["sketch2cad-api/src/sketch2cad/calibration/__init__.py", "sketch2cad-api/src/sketch2cad/calibration/service.py", "sketch2cad-api/src/sketch2cad/api/routers/calibration.py", "sketch2cad-api/tests/calibration/test_calibration_api.py"]
parallelizable: false
estimate: { optimistic: 8, likely: 13, pessimistic: 22 }
labels: [ai-sdlc, python]
---

## Description

`PUT /plans/{id}/calibration` computes and returns `image_to_paper`, `reprojection_error_mm {max, rms}` and `meets_tolerance` without persisting. `POST /plans/{id}/calibration/accept {as_metric}` persists, stores points, matrix and errors, moves the plan to `EDITING` and emits `calibration.proposed` and `calibration.accepted`. Reject `as_metric` when the error exceeds `calibration.max_reprojection_error_mm` (422, V-05 message) and when method is `none`. Reject any change when entities exist (409, V-08 message). Run commands from the `sketch2cad-api/` folder.

Implements: REQ-03, US-03, V-05, V-08

## Acceptance Criteria

- [ ] `as_metric=true` with a 1.3 mm error returns 422 whose detail contains "supera el máximo" (H2-2, `tests/calibration/test_calibration_api.py`).
- [ ] Method `none` leaves `metric_status == "non_metric"`.
- [ ] With one entity in the plan, `PUT /calibration` returns 409 with the V-08 message (H2-4).
- [ ] Accepted calibration stores `control_points`, the 3x3 matrix and `reprojection_error_mm.max` and `.rms`.
