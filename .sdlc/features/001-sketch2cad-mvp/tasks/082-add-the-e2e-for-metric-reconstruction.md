---
id: 001-082
title: "Add the E2E for metric reconstruction"
status: partial
unmet: ["Schematic-sketch triangulation to 'Métrico' (H3-10 scenario and 1 cm check only runs in the scaled_drawing test): blocked by the product gap that a schematic sketch cannot produce a first metric reference point; test.fixme left in e2e/triangulation.spec.ts"]
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-080, 001-081, 001-079]
implements: [US-08, REQ-06, REQ-08, REQ-07]
files: ["sketch2cad-web/e2e/triangulation.spec.ts", "sketch2cad-web/e2e/fixtures/schematic_plan.json"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, typescript]
---

## Description

Fix two wall lengths, locate an evidence point by triangulation, see the evidence table and the status become `metric` after resolving the blockers (H3-10). Run commands from the `sketch2cad-web/` folder.

Implements: US-08, REQ-06, REQ-08, REQ-07

## Acceptance Criteria

- [ ] `pnpm exec playwright test e2e/triangulation.spec.ts` passes against the full stack.
- [ ] The test asserts the point's coordinates against hand-calculated values within 1 cm.
