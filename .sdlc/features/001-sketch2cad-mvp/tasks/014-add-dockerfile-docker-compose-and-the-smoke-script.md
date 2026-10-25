---
id: 001-014
title: "Add Dockerfile, docker-compose and the smoke script"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-013, 001-012]
implements: [REQ-16]
files: ["sketch2cad-api/Dockerfile", "sketch2cad-api/docker-compose.yml", "sketch2cad-api/.dockerignore", "sketch2cad-api/scripts/compose_smoke.sh", "docs/running-locally.md"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

Services `db` (postgres:16, named volume), `api` (uvicorn), `worker` (procrastinate worker) and `web` (build context `../sketch2cad-web`), each with a healthcheck. Secrets only through environment. `compose_smoke.sh` runs `docker compose up -d --wait`, measures elapsed time, calls `/healthz` and exits non-zero above 90 s. Covers H0-1. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-16

## Acceptance Criteria

- [ ] `bash scripts/compose_smoke.sh` exits 0 and prints the elapsed seconds; the value is below 90 on the development machine.
- [ ] `docker compose ps --format json` reports `Health: healthy` for `db`, `api`, `worker` and `web`.
- [ ] `rg -n 'password|secret' docker-compose.yml` shows only references to `${...}` variables, no literal values.
