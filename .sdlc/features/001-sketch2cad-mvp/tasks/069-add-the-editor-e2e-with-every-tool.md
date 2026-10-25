---
id: 001-069
title: "Add the editor E2E with every tool"
status: partial
unmet: ["Measure step of H2-11 (CU-04 medir): the editor has no measure tool, so the E2E asserts nothing for it"]
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-067, 001-064, 001-068]
implements: [REQ-05, US-06, REQ-10, US-03]
files: ["sketch2cad-web/e2e/editor_tools.spec.ts", "sketch2cad-web/e2e/fixtures/sample_plan.json"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 18 }
labels: [ai-sdlc, typescript]
---

## Description

Opens the sample plan and exercises line, polyline, point, text, symbol, vertex move, delete, layer reclassify, measure and undo; checks entity state and the event log after each step. Covers H2-11. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05, US-06, REQ-10, US-03

## Acceptance Criteria

- [ ] `pnpm exec playwright test e2e/editor_tools.spec.ts` passes against the full stack.
- [ ] The test asserts one event per mutation through `GET /case-files/{id}/events`.
