---
id: 001-051
title: "Add the rectify worker job"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-050]
implements: [REQ-03]
files: ["sketch2cad-api/src/sketch2cad/worker/tasks_rectify.py", "sketch2cad-api/src/sketch2cad/calibration/rectify.py", "sketch2cad-api/tests/calibration/test_rectify.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

`rectify` warps the original into a canvas of paper millimetres at 10 px/mm (`cv2.warpPerspective`) and stores it as artifact kind `rectified_image`. Enqueued on calibration acceptance. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-03

## Acceptance Criteria

- [ ] The rectified synthetic pattern measures 2000 × 1500 px ± 2 px for a 200 × 150 mm paper (`tests/calibration/test_rectify.py`).
- [ ] Accepting calibration creates exactly one `rectified_image` artifact; accepting twice does not create a second job (idempotent).
