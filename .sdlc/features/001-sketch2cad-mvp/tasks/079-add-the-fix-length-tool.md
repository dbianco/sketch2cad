---
id: 001-079
title: "Add the fix-length tool"
status: in_review
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-078]
implements: [US-07, REQ-06, V-04]
files: ["sketch2cad-web/src/editor/tools/FixLengthTool.tsx", "sketch2cad-web/src/editor/tools/FixLengthTool.test.tsx", "sketch2cad-web/src/editor/util/parseDistance.ts"]
parallelizable: false
estimate: { optimistic: 4, likely: 6, pessimistic: 10 }
labels: [ai-sdlc, typescript]
---

## Description

Select segment, click the anchor vertex, enter the value; commas and dots accepted; invalid values show the V-04 message. Run commands from the `sketch2cad-web/` folder.

Implements: US-07, REQ-06, V-04

## Acceptance Criteria

- [ ] `pnpm test --run src/editor/tools/FixLengthTool.test.tsx` passes: "4,20" sends `value_m = 4.2`; "0" shows "Ingresa una distancia numérica mayor que cero, por ejemplo 3,50." and sends nothing.
