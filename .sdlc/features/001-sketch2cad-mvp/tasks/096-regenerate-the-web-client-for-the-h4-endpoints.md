---
id: 001-096
title: "Regenerate the web client for the H4 endpoints"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-093]
implements: [INFRA]
files: ["sketch2cad-web/src/api/schema.d.ts"]
parallelizable: false
estimate: { optimistic: 1, likely: 1, pessimistic: 2 }
labels: [ai-sdlc, typescript]
---

## Description

Regenerate the client from `../sketch2cad-api/openapi.json`. Run commands from the `sketch2cad-web/` folder.

Implements: INFRA

## Acceptance Criteria

- [ ] `bash scripts/generate-client.sh && git diff --exit-code src/api/schema.d.ts` exits 0.
- [ ] `rg -n 'exports|artifacts' src/api/schema.d.ts` finds both paths.
