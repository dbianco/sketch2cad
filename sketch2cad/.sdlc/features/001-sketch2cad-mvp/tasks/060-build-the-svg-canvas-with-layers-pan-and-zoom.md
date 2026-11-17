---
id: 001-060
title: "Build the SVG canvas with layers, pan and zoom"
status: in_review
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-059]
implements: [REQ-05]
files: ["sketch2cad-web/src/editor/canvas/EditorCanvas.tsx", "sketch2cad-web/src/editor/canvas/layers.ts", "sketch2cad-web/src/editor/canvas/viewport.ts", "sketch2cad-web/src/editor/canvas/EditorCanvas.test.tsx"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, typescript]
---

## Description

Render entities as SVG elements grouped by layer, source image as `<image>` with opacity control, pan and zoom by updating the `<svg>` `viewBox` (decision D-14; never a CSS transform of the wrapper, which breaks `vector-effect: non-scaling-stroke`). Hit-testing by native SVG events. Coordinates convert between world metres (Y up) and screen (Y down) in `viewport.ts`. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05

## Acceptance Criteria

- [ ] `viewport.ts` round-trips `worldToScreen(screenToWorld(p))` within 1e-9 for 1000 random points (unit test).
- [ ] Toggling a layer's visibility hides its elements (`pnpm test --run src/editor/canvas/EditorCanvas.test.tsx`).
- [ ] The source image opacity slider changes the `opacity` attribute between 0 and 1.
