---
id: 001-041
title: "Regenerate the web client for the H1 endpoints"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-035, 001-038, 001-040, 001-024]
implements: [INFRA]
files: ["sketch2cad-web/src/api/schema.d.ts"]
parallelizable: false
estimate: { optimistic: 1, likely: 1, pessimistic: 2 }
labels: [ai-sdlc, typescript]
---

## Description

After the H1 API changes, regenerate `src/api/schema.d.ts` from `../sketch2cad-api/openapi.json`. Run commands from the `sketch2cad-web/` folder.

Implements: INFRA

## Acceptance Criteria

- [ ] `bash scripts/generate-client.sh && git diff --exit-code src/api/schema.d.ts` exits 0 after the update.
- [ ] `rg -n 'sources' src/api/schema.d.ts` finds the `/api/v1/case-files/{id}/sources` path.
