---
id: 001-003
title: "Add SQLAlchemy engine, session and declarative base"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-001]
implements: [INFRA]
files: ["sketch2cad-api/src/sketch2cad/db/__init__.py", "sketch2cad-api/src/sketch2cad/db/session.py", "sketch2cad-api/src/sketch2cad/db/base.py", "sketch2cad-api/tests/conftest.py", "sketch2cad-api/tests/test_db_session.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 4, pessimistic: 7 }
labels: [ai-sdlc, python]
---

## Description

Create the psycopg 3 engine from `DATABASE_URL`, a `get_session` dependency and a `Base` with naming conventions for constraints. `tests/conftest.py` provides a PostgreSQL fixture that creates an isolated schema per test session (database provided by the CI service or the local compose). Jot Down: Milestone 1, section 3. Run commands from the `sketch2cad-api/` folder.

Implements: INFRA

## Acceptance Criteria

- [ ] `uv run pytest tests/test_db_session.py -q` passes against a real PostgreSQL 16 and executes `SELECT 1` through `get_session`.
- [ ] Constraint naming conventions produce names matching `^(pk|fk|uq|ix|ck)_` for every constraint created in the test (asserted in the test).
- [ ] `rg -n 'sqlite' src/ tests/` returns no matches (tests run on PostgreSQL only).
