---
id: 001-053
title: "Implement the local coordinate frame and rigid transform"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-052]
implements: [REQ-04, US-05]
files: ["sketch2cad-api/src/sketch2cad/geometry/frame.py", "sketch2cad-api/src/sketch2cad/plans/local_frame.py", "sketch2cad-api/src/sketch2cad/api/routers/plans.py", "sketch2cad-api/tests/plans/test_local_frame.py"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, python]
---

## Description

`PUT /plans/{id}/local-frame {origin_paper_mm, x_axis_angle_deg, north_angle_deg}`. When entities exist, a change of origin or X axis applies one rigid transform (translation + rotation) to every `coords_m` of the draft revision in one transaction and writes a single `local_frame.changed` event holding the transform. A North change touches metadata only. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-04, US-05

## Acceptance Criteria

- [ ] With 50 entities, pairwise distances before and after a frame change differ by ≤ 1e-9 m and exactly one `local_frame.changed` event exists (H2-5, `tests/plans/test_local_frame.py`).
- [ ] Changing only `north_angle_deg` leaves every `coords_m` byte-identical (H2-6).
- [ ] First-time frame setting writes `local_frame.set`, not `local_frame.changed`.
