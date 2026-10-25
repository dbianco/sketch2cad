---
id: 001-001
title: "Scaffold the sketch2cad-api folder"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: []
implements: [INFRA]
files: ["sketch2cad-api/pyproject.toml", "sketch2cad-api/uv.lock", "sketch2cad-api/.python-version", "sketch2cad-api/.gitignore", "sketch2cad-api/.env.example", "sketch2cad-api/README.md", "sketch2cad-api/src/sketch2cad/__init__.py", "sketch2cad-api/src/sketch2cad/config.py", "sketch2cad-api/tests/test_config.py"]
parallelizable: true
estimate: { optimistic: 3, likely: 5, pessimistic: 8 }
labels: [ai-sdlc, python]
---

## Description

Create the `sketch2cad-api/` folder inside the existing sketch2cad repository (single-repository decision D-1). Create the Python 3.12 project with `uv` (decision D-5). Add `pyproject.toml` with FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, psycopg 3, argon2-cffi, Procrastinate, ruff, pyright and pytest. `config.py` reads `DATABASE_URL`, `SESSION_SECRET`, `SIGNING_KEY_PATH`, `APP_ENV` from the environment with pydantic-settings and never from code. `.env.example` lists the names with empty values. Jot Down: Milestone 1, sections 3 and 5. Run commands from the `sketch2cad-api/` folder.

Implements: INFRA

## Acceptance Criteria

- [ ] `uv sync --frozen` succeeds on a clean checkout and creates `uv.lock`-consistent environment (`uv lock --check` exits 0).
- [ ] `uv run pytest tests/test_config.py -q` passes; the test asserts that a missing `DATABASE_URL` raises a validation error naming the variable.
- [ ] `rg -n 'SESSION_SECRET\s*=\s*["\']' src/` returns no matches (no secret literals in code).
- [ ] `uv run ruff check .` and `uv run pyright` exit 0.
