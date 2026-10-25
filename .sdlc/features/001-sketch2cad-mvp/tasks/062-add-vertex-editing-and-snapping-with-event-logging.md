---
id: 001-062
title: "Add vertex editing and snapping with event logging"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-061]
implements: [REQ-05, US-06]
files: ["sketch2cad-web/src/editor/tools/VertexEditTool.tsx", "sketch2cad-web/src/editor/snapping/snap.ts", "sketch2cad-web/src/editor/snapping/snap.test.ts", "sketch2cad-web/src/editor/panels/SnapSettings.tsx"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, typescript]
---

## Description

Dragging a vertex sends `PATCH` with `operation = move_vertex`. Snapping to endpoints, orthogonal (0° and 90° relative to local axes) and grid; each applied snap sends its own operation name so the audit log records it. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05, US-06

## Acceptance Criteria

- [ ] `snap.ts` unit tests: a point within 0.5° of horizontal snaps to exactly horizontal; outside the tolerance it does not (`pnpm test --run src/editor/snapping/snap.test.ts`).
- [ ] Applying orthogonal snap issues a request with `operation = "snap_orthogonal"` (asserted on the mocked client).
- [ ] Grid size is configurable and persists in `localStorage` key `s2c.grid`.
