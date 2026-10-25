---
id: 001-005
title: "Add migration round-trip test and CI-ready script"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-004]
implements: [REQ-16]
files: ["sketch2cad-api/scripts/migration_roundtrip.sh", "sketch2cad-api/tests/test_migration_roundtrip.py"]
parallelizable: false
estimate: { optimistic: 1, likely: 2, pessimistic: 4 }
labels: [ai-sdlc, python]
---

## Description

Script runs `alembic upgrade head`, `alembic downgrade base`, `alembic upgrade head` against `DATABASE_URL` and fails on any error. The pytest wrapper runs it in a throwaway database. Covers criterion H0-5. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-16

## Acceptance Criteria

- [ ] `bash scripts/migration_roundtrip.sh` exits 0 on a clean database and prints the three revision steps.
- [ ] Running the script after inserting a deliberately broken `downgrade()` in a temporary revision exits non-zero (asserted in `tests/test_migration_roundtrip.py` using a temp copy of the migrations folder).
