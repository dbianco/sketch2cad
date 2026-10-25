---
id: 001-058
title: "Regenerate the web client for the H2 endpoints"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-056, 001-053, 001-051, 001-047, 001-057]
implements: [INFRA]
files: ["sketch2cad-web/src/api/schema.d.ts"]
parallelizable: false
estimate: { optimistic: 1, likely: 1, pessimistic: 2 }
labels: [ai-sdlc, typescript]
---

## Description

Regenerate the client after the H2 API changes. Run commands from the `sketch2cad-web/` folder.

Implements: INFRA

## Acceptance Criteria

- [ ] `bash scripts/generate-client.sh && git diff --exit-code src/api/schema.d.ts` exits 0.
- [ ] `rg -n 'local-frame|calibration/accept|transition' src/api/schema.d.ts` finds all three paths.
