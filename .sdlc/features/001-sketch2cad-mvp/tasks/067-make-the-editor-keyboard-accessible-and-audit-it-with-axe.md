---
id: 001-067
title: "Make the editor keyboard-accessible and audit it with axe"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-062, 001-063, 001-066, 001-065]
implements: [REQ-05]
files: ["sketch2cad-web/src/editor/a11y/shortcuts.ts", "sketch2cad-web/src/editor/a11y/KeyboardHelp.tsx", "sketch2cad-web/e2e/editor_a11y.spec.ts"]
parallelizable: false
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, typescript]
---

## Description

Keyboard path to create a line (select tool with a key, move a virtual cursor with arrows, confirm with Enter), documented in `KeyboardHelp`. Axe scan on editor, calibration and review pages. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05

## Acceptance Criteria

- [ ] `pnpm exec playwright test e2e/editor_a11y.spec.ts` passes: a line is created using only the keyboard (H2-14).
- [ ] `@axe-core/playwright` reports 0 violations with impact `critical` or `serious` on the three pages.
