---
id: 001-077
title: "Implement the evidence registry endpoints"
status: in_review
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-070, 001-032]
implements: [REQ-08, US-10]
files: ["sketch2cad-api/src/sketch2cad/evidence/__init__.py", "sketch2cad-api/src/sketch2cad/evidence/service.py", "sketch2cad-api/src/sketch2cad/api/routers/evidence.py", "sketch2cad-api/tests/evidence/test_evidence.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

`GET/POST /case-files/{id}/evidence`, `PATCH /evidence/{id}`; numbers assigned sequentially per case without gaps or reuse; events `evidence.created` and `evidence.updated`. Symbols and points reference an `evidence_id`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-08, US-10

## Acceptance Criteria

- [ ] Two plans in the same case referencing the same evidence show the same number; two cases may both have number 1 (H3-9, `tests/evidence/test_evidence.py`).
- [ ] Two concurrent creations produce numbers 1 and 2 with no duplicate.
