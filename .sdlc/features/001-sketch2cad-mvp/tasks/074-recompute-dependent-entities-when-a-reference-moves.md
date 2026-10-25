---
id: 001-074
title: "Recompute dependent entities when a reference moves"
status: in_review
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-073]
implements: [REQ-06]
files: ["sketch2cad-api/src/sketch2cad/entities/recompute.py", "sketch2cad-api/src/sketch2cad/entities/service.py", "sketch2cad-api/tests/entities/test_recompute.py"]
parallelizable: false
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

After any geometry change, find entities whose `constraint.refs` include the changed entity (breadth-first, cycle-safe), recompute and write `entity.recomputed` with before and after. Moving a constrained entity by hand removes its constraint after confirmation (`confirm_constraint_removal=true`) and sets `metric_origin = none`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-06

## Acceptance Criteria

- [ ] Moving reference A updates the dependent point and creates one `entity.recomputed` event with before and after (H3-5, `tests/entities/test_recompute.py`).
- [ ] Moving a constrained entity without `confirm_constraint_removal` returns 409; with it, the constraint is removed and `metric_origin == "none"`.
