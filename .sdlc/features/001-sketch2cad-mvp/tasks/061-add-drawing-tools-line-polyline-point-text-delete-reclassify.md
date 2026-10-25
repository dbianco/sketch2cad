---
id: 001-061
title: "Add drawing tools: line, polyline, point, text, delete, reclassify"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-060]
implements: [REQ-05, US-06]
files: ["sketch2cad-web/src/editor/tools/LineTool.tsx", "sketch2cad-web/src/editor/tools/PolylineTool.tsx", "sketch2cad-web/src/editor/tools/PointTextTools.tsx", "sketch2cad-web/src/editor/tools/SelectTool.tsx", "sketch2cad-web/src/editor/tools/tools.test.tsx", "sketch2cad-web/src/editor/panels/Palette.tsx"]
parallelizable: false
estimate: { optimistic: 10, likely: 16, pessimistic: 26 }
labels: [ai-sdlc, typescript]
---

## Description

Tool palette with keyboard shortcuts. Select tool picks entities, deletes and reclassifies layer. Vertex dragging is in the next task. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05, US-06

## Acceptance Criteria

- [ ] `pnpm test --run src/editor/tools/tools.test.tsx` passes: each tool creates the matching entity with the layer chosen in the panel and calls the store.
- [ ] Every palette button has an `aria-label` and is reachable with Tab (asserted).
