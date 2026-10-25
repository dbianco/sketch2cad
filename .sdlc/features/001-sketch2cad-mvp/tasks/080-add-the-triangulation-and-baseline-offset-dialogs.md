---
id: 001-080
title: "Add the triangulation and baseline-offset dialogs"
status: todo
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-078]
implements: [US-08, US-09, V-06, V-07, V-04]
files: ["sketch2cad-web/src/editor/tools/TriangulationDialog.tsx", "sketch2cad-web/src/editor/tools/BaselineOffsetDialog.tsx", "sketch2cad-web/src/editor/tools/constraintDialogs.test.tsx", "sketch2cad-web/src/editor/geometry/preview.ts"]
parallelizable: false
estimate: { optimistic: 8, likely: 13, pessimistic: 22 }
labels: [ai-sdlc, typescript]
---

## Description

Follows prototype screen 4 side panel. Client-side preview of both circle intersections using the same formulas (the server stays authoritative); shows both candidates, preselects the nearest, shows the V-06 and V-07 messages from the 422 responses. Run commands from the `sketch2cad-web/` folder.

Implements: US-08, US-09, V-06, V-07, V-04

## Acceptance Criteria

- [ ] `pnpm test --run src/editor/tools/constraintDialogs.test.tsx` passes: 2 solutions render 2 candidate markers with the nearest selected; a 422 renders "Estas dos distancias no se cruzan. Revisa las medidas o las referencias elegidas.".
- [ ] `preview.ts` agrees with five server fixtures (same inputs and expected points as the API tests) within 1e-6 m.
