---
id: 001-070
title: "Add the evidence table and constraint columns"
status: todo
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-069, 001-056, 001-057]
implements: [REQ-08, REQ-06]
files: ["sketch2cad-api/migrations/versions/0005_evidence_constraints.py", "sketch2cad-api/src/sketch2cad/db/models/evidence.py", "sketch2cad-api/src/sketch2cad/db/models/entity.py", "sketch2cad-api/tests/test_models_evidence.py"]
parallelizable: true
estimate: { optimistic: 3, likely: 5, pessimistic: 8 }
labels: [ai-sdlc, python]
---

## Description

`evidence (id, case_file_id, number, label, attributes, created_by, created_at)` with unique `(case_file_id, number)`; `entity.evidence_id` as FK; `entity.constraint` JSONB structure for `triangulation` and `baseline_offset`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-08, REQ-06

## Acceptance Criteria

- [ ] Round-trip migration passes (`scripts/migration_roundtrip.sh`).
- [ ] Inserting two evidence rows with the same number in one case fails; the same number in two cases succeeds (`tests/test_models_evidence.py`).
