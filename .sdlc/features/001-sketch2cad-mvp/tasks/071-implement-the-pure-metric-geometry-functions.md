---
id: 001-071
title: "Implement the pure metric geometry functions"
status: in_review
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-048]
implements: [REQ-06, US-07, US-08, US-09, V-04]
files: ["sketch2cad-api/src/sketch2cad/geometry/constraints.py", "sketch2cad-api/src/sketch2cad/geometry/metrics.py", "sketch2cad-api/tests/geometry/test_constraints.py", "sketch2cad-api/tests/geometry/test_cases_by_hand.py"]
parallelizable: true
estimate: { optimistic: 10, likely: 16, pessimistic: 28 }
labels: [ai-sdlc, python]
---

## Description

`circle_intersections`, `baseline_offset_point`, `scale_from_anchor`, `has_dependency_cycle`, `parse_distance` (accepts comma or dot, rejects ≤ 0 with the V-04 message). Degenerate cases return structured results (0, 1 or 2 solutions) instead of raising. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-06, US-07, US-08, US-09, V-04

## Acceptance Criteria

- [ ] At least 5 hand-solved triangulation and baseline-offset cases match within 1 cm (CA-03, H3-1, `tests/geometry/test_cases_by_hand.py`).
- [ ] A hypothesis test shows each returned point is at distance `d_a` from A and `d_b` from B within 1e-9 m.
- [ ] Tangent circles return 1 solution; disjoint and concentric circles return 0 (H3-2).
- [ ] `parse_distance('3,50') == 3.5` and `parse_distance('0')` raises with the V-04 message.
