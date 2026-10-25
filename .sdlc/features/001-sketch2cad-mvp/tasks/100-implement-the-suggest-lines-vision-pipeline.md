---
id: 001-100
title: "Implement the suggest_lines vision pipeline"
status: todo
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-099]
implements: [REQ-17]
files: ["sketch2cad-api/src/sketch2cad/vision/__init__.py", "sketch2cad-api/src/sketch2cad/vision/binarize.py", "sketch2cad-api/src/sketch2cad/vision/skeleton.py", "sketch2cad-api/src/sketch2cad/vision/simplify.py", "sketch2cad-api/tests/vision/test_pipeline_unit.py"]
parallelizable: false
estimate: { optimistic: 12, likely: 20, pessimistic: 36 }
labels: [ai-sdlc, python]
---

## Description

Gray → `cv2.adaptiveThreshold` (Gaussian, block 31, C 10) → 3×3 morphological opening → `skeletonize` → `sknw` graph → paths between nodes → `shapely.simplify` at 0.8 mm paper → drop paths under 5 mm → merge collinear within 5°. Parameters from configuration. Output polylines in rectified pixels with `confidence = f(length, straightness)`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-17

## Acceptance Criteria

- [ ] On a synthetic image with three known segments, the pipeline returns three polylines with vertex deviation < 2 mm paper (`tests/vision/test_pipeline_unit.py`).
- [ ] All returned polylines have length ≥ 5 mm paper.
- [ ] Every `confidence` is in [0, 1].
