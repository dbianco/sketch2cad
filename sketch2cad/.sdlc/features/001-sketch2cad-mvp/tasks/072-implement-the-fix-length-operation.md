---
id: 001-072
title: "Implement the fix-length operation"
status: in_review
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-071, 001-052]
implements: [REQ-06, US-07]
files: ["sketch2cad-api/src/sketch2cad/entities/ops_fix_length.py", "sketch2cad-api/src/sketch2cad/entities/service.py", "sketch2cad-api/tests/entities/test_fix_length.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

`PATCH /entities/{id}` with `operation = fix_length {anchor_vertex, value_m}`: scales the segment from the anchor keeping direction, moves the free vertex and rigidly the vertices on the same side; sets `metric_origin = declared_dimension`; polylines are `declared_dimension` only when every segment has a declared length. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-06, US-07

## Acceptance Criteria

- [ ] Fixing 4.20 m on a 3.00 m segment gives length 4.20 ± 1e-9 m and shifts later vertices by the same vector (H3-6, `tests/entities/test_fix_length.py`).
- [ ] A polyline with 2 of 3 segments declared stays `trace_only`.
- [ ] A value of `0` or negative returns 422 with the V-04 message.
