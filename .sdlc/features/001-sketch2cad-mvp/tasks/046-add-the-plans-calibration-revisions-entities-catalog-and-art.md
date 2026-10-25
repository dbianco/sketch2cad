---
id: 001-046
title: "Add the plans, calibration, revisions, entities, catalog and artifacts migration"
status: in_review
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-040, 001-045, 001-044]
implements: [REQ-03, REQ-05, REQ-09, REQ-10]
files: ["sketch2cad-api/migrations/versions/0003_plans_editor.py", "sketch2cad-api/src/sketch2cad/db/models/plan.py", "sketch2cad-api/src/sketch2cad/db/models/calibration.py", "sketch2cad-api/src/sketch2cad/db/models/revision.py", "sketch2cad-api/src/sketch2cad/db/models/entity.py", "sketch2cad-api/src/sketch2cad/db/models/catalog.py", "sketch2cad-api/src/sketch2cad/db/models/artifact.py", "sketch2cad-api/tests/test_models_plans_schema.py"]
parallelizable: true
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, python]
---

## Description

Tables as in Jot Down Milestone 3, section 3 (enums for plan status and metric status; one active calibration per plan; `entity.deleted_at` soft delete). Trigger that rejects any `UPDATE` of a `revision` row with `state = 'frozen'`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-03, REQ-05, REQ-09, REQ-10

## Acceptance Criteria

- [ ] `alembic upgrade head` then `downgrade -1` round-trip passes (`scripts/migration_roundtrip.sh` exits 0).
- [ ] Updating a frozen revision's `snapshot` fails with a database error (`tests/test_models_plans_schema.py`).
- [ ] The `plan.status` enum contains exactly the nine states of Spec §9.2, asserted against a constant list.
