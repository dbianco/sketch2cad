---
id: 001-093
title: "Add export jobs, the artifacts endpoints and the final-export guards"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-091, 001-056]
implements: [REQ-12, REQ-13, REQ-14, US-15, US-16, V-11]
files: ["sketch2cad-api/src/sketch2cad/worker/tasks_export.py", "sketch2cad-api/src/sketch2cad/api/routers/exports.py", "sketch2cad-api/src/sketch2cad/exports/service.py", "sketch2cad-api/tests/exports/test_export_api.py"]
parallelizable: false
estimate: { optimistic: 8, likely: 14, pessimistic: 24 }
labels: [ai-sdlc, python]
---

## Description

`POST /plans/{id}/exports {mode, paper, scale}` returns 202 with a job. `final` requires state `VALIDATED` and permission `case_file:deliver` (409 with the V-11 message otherwise; 403 without permission). Completing a final export moves the plan to `EXPORTED`. `GET /artifacts/{id}` without `confirm=true` returns the manifest and hashes; with it, downloads and writes `artifact.downloaded`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-12, REQ-13, REQ-14, US-15, US-16, V-11

## Acceptance Criteria

- [ ] `mode=final` on an `EDITING` plan returns 409 with "La exportación final requiere un plano validado."; without `deliver` it returns 403 (H4-6, `tests/exports/test_export_api.py`).
- [ ] Download without `confirm=true` creates no `artifact.downloaded` event; with it creates exactly one (H4-8).
- [ ] A draft export does not change the plan state.
