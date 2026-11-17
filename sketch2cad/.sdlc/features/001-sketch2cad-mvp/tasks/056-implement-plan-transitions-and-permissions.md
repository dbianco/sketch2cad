---
id: 001-056
title: "Implement plan transitions and permissions"
status: in_review
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-054, 001-055]
implements: [REQ-10, US-14, V-09, V-10]
files: ["sketch2cad-api/src/sketch2cad/plans/transitions.py", "sketch2cad-api/src/sketch2cad/api/routers/plans.py", "sketch2cad-api/tests/plans/test_transitions.py"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, python]
---

## Description

Table-driven transitions from Spec §9.2 with role per transition. `POST /plans/{id}/transition {to, reason?}`: rejection requires a reason (422, V-09 message); technician cannot validate (403, V-09 message); editing a validated plan returns 409 with the V-10 message. Emits `plan.transition`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-10, US-14, V-09, V-10

## Acceptance Criteria

- [ ] Every (from, to) pair outside the §9.2 table returns 409; the test iterates the full 9×9 matrix (H2-7, `tests/plans/test_transitions.py`).
- [ ] A technician calling `to=VALIDATED` receives 403 with "Solo un revisor puede validar este plano.".
- [ ] `PATCH /entities/{id}` on a `VALIDATED` plan returns 409 with "Este plano está validado. Reábrelo para editarlo.".
