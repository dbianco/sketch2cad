---
id: 001-073
title: "Implement triangulation and baseline-offset operations with constraints"
status: in_review
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-071, 001-072]
implements: [REQ-06, US-08, US-09, V-06, V-07]
files: ["sketch2cad-api/src/sketch2cad/entities/ops_triangulate.py", "sketch2cad-api/src/sketch2cad/entities/ops_baseline.py", "sketch2cad-api/src/sketch2cad/entities/service.py", "sketch2cad-api/tests/entities/test_constraints_ops.py"]
parallelizable: false
estimate: { optimistic: 8, likely: 13, pessimistic: 22 }
labels: [ai-sdlc, python]
---

## Description

`operation = triangulate {ref_a, ref_b, d_a_m, d_b_m, solution}` returns both candidate points and applies the chosen one (default nearest); `operation = baseline_offset {base_a, base_b, s_m, d_m, side}`. Both store `constraint`, set `metric_origin`, and reject with the V-06 and V-07 messages without changing anything. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-06, US-08, US-09, V-06, V-07

## Acceptance Criteria

- [ ] Non-intersecting distances return 422 containing "no se cruzan" and the entity coordinates are unchanged (H3-2, `tests/entities/test_constraints_ops.py`).
- [ ] A reference with `metric_origin = trace_only` returns 422 with the V-07 message (H3-3).
- [ ] A reference that depends on the entity being fixed (cycle) returns 422 (H3-4).
