---
id: 001-004
title: "Create Alembic setup and the initial migration (user, session, case_file, case_member)"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-003]
implements: [REQ-01, REQ-16]
files: ["sketch2cad-api/alembic.ini", "sketch2cad-api/migrations/env.py", "sketch2cad-api/migrations/versions/0001_initial.py", "sketch2cad-api/src/sketch2cad/db/models/__init__.py", "sketch2cad-api/src/sketch2cad/db/models/user.py", "sketch2cad-api/src/sketch2cad/db/models/case_file.py", "sketch2cad-api/tests/test_models_schema.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

Define the four tables exactly as in Jot Down Milestone 1, section 3: `user` (role enum technician/reviewer/admin, `failed_attempts`, `locked_until`), `session` (`token_sha256` unique, `csrf_token`, `expires_at`, `revoked_at`), `case_file` (`code` unique, `incident_type` enum, reserved `retention_policy_id` and `legal_hold` default false) and `case_member` (composite PK, `permissions` text[]). Include a per-year sequence for case codes. Provide both `upgrade` and `downgrade`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01, REQ-16

## Acceptance Criteria

- [ ] `uv run alembic upgrade head` on an empty database creates exactly the tables `user`, `session`, `case_file`, `case_member` (asserted by `tests/test_models_schema.py` querying `information_schema.tables`).
- [ ] `uv run alembic downgrade base` followed by the same query returns zero application tables.
- [ ] `case_file.code` has a unique constraint and `case_file.legal_hold` defaults to `false` (asserted in the test).
