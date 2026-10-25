---
id: 001-048
title: "Implement image-to-paper and paper-to-world transforms"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-001]
implements: [REQ-03, REQ-04]
files: ["sketch2cad-api/src/sketch2cad/geometry/__init__.py", "sketch2cad-api/src/sketch2cad/geometry/transforms.py", "sketch2cad-api/src/sketch2cad/geometry/homography.py", "sketch2cad-api/tests/geometry/test_transforms.py"]
parallelizable: true
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, python]
---

## Description

Pure functions: `scan_dpi_scale`, `scan_segment_scale`, `homography_from_4_points` (`cv2.findHomography`), `apply_h`, reprojection error (max, RMS in mm), `paper_to_world(mm, paper_scale)` and Y inversion. Axes: image and paper Y down, world Y up (Spec §11.1). Run commands from the `sketch2cad-api/` folder.

Implements: REQ-03, REQ-04

## Acceptance Criteria

- [ ] For a synthetic 200 × 150 mm pattern warped with `cv2.warpPerspective`, rectified distances differ from truth by ≤ max(1 mm, 1 %) and the reported max reprojection error is ≤ 1.0 mm (H2-1, `tests/geometry/test_transforms.py`).
- [ ] A hypothesis property test shows `inverse(apply_h(p)) == p` within 1e-6 for random points.
- [ ] `paper_to_world((100, 50), 100)` returns `(10.0, -5.0)` ± 1e-9 m.
