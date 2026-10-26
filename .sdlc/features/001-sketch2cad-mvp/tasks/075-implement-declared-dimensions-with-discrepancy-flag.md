---
id: 001-075
title: "Implement declared dimensions with discrepancy flag"
status: in_review
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-052]
implements: [REQ-06]
files: ["sketch2cad-api/src/sketch2cad/entities/dimensions.py", "sketch2cad-api/src/sketch2cad/entities/schemas.py", "sketch2cad-api/tests/entities/test_dimensions.py"]
parallelizable: true
estimate: { optimistic: 3, likely: 5, pessimistic: 9 }
labels: [ai-sdlc, python]
---

## Description

`dimension` entities compute `measured_value_m` from `p1_m`, `p2_m`; `discrepancy = |declared - measured| > dimension.discrepancy_tolerance_m` (default 0.02). The server never edits either value. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-06

## Acceptance Criteria

- [ ] Declared 4.20 with measured 4.24 gives `discrepancy == true`; measured 4.215 gives false (H3-7, `tests/entities/test_dimensions.py`).
- [ ] Neither `declared_value_m` nor `measured_value_m` changes after the check.
