---
id: 001-065
title: "Add the local frame tool and north arrow"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-060, 001-058]
implements: [US-05, REQ-04]
files: ["sketch2cad-web/src/editor/tools/LocalFrameTool.tsx", "sketch2cad-web/src/editor/canvas/NorthArrow.tsx", "sketch2cad-web/src/editor/tools/LocalFrameTool.test.tsx"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, typescript]
---

## Description

Click to set origin, drag to set +X direction, numeric field for North angle. Changing origin or axis calls the local-frame endpoint and reloads entities; changing North only moves the arrow. Run commands from the `sketch2cad-web/` folder.

Implements: US-05, REQ-04

## Acceptance Criteria

- [ ] `pnpm test --run src/editor/tools/LocalFrameTool.test.tsx` passes: changing North sends one request and does not reload entities; changing the origin reloads them.
- [ ] The north arrow rotates by the given angle (checked via the `transform` attribute).
