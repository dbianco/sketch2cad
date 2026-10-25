---
id: 001-044
title: "Add the E2E for ingestion"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-043, 001-038, 001-035]
implements: [REQ-02, US-02, REQ-01]
files: ["sketch2cad-web/e2e/ingest.spec.ts", "sketch2cad-web/e2e/fixtures/synthetic_sketch.png"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 9 }
labels: [ai-sdlc, typescript]
---

## Description

Log in, create a case, upload the synthetic image, wait for the QC result and open the viewer. Runs against the real stack and checks the downloaded original hash. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-02, US-02, REQ-01

## Acceptance Criteria

- [ ] `pnpm exec playwright test e2e/ingest.spec.ts` passes against the compose stack (H1-10).
- [ ] The test compares the SHA-256 of the downloaded original with the one displayed in the UI.
