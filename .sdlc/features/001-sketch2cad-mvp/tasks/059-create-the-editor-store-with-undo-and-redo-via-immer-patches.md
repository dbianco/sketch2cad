---
id: 001-059
title: "Create the editor store with undo and redo via Immer patches"
status: in_review
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-058]
implements: [REQ-05, US-06]
files: ["sketch2cad-web/src/editor/store/entities.ts", "sketch2cad-web/src/editor/store/history.ts", "sketch2cad-web/src/editor/store/types.ts", "sketch2cad-web/src/editor/store/history.test.ts"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, typescript]
---

## Description

Zustand store with normalized entities and `produceWithPatches`; undo and redo apply inverse patches. Each committed operation is queued to the API with its `operation` name. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05, US-06

## Acceptance Criteria

- [ ] Moving a vertex then undo restores coordinates with strict equality (`toEqual` on the full array, H2-10), and redo reapplies it (`pnpm test --run src/editor/store/history.test.ts`).
- [ ] A property test (fast-check) applies 50 random operations then undoes all 50 and equals the initial state.
