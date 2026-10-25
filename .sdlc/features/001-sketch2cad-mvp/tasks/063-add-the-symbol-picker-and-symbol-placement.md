---
id: 001-063
title: "Add the symbol picker and symbol placement"
status: in_review
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-061, 001-047]
implements: [REQ-09, REQ-05]
files: ["sketch2cad-web/src/editor/tools/SymbolTool.tsx", "sketch2cad-web/src/editor/panels/SymbolPicker.tsx", "sketch2cad-web/src/editor/panels/SymbolPicker.test.tsx", "sketch2cad-web/src/editor/canvas/symbols.ts"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, typescript]
---

## Description

Loads `GET /catalog/active`, shows the 8 symbols with inline SVG, places a symbol with rotation and attribute form (required `evidence_number` for `evidence_marker`). `size_mode` decides whether the symbol scales with the drawing (`world_m`) or stays constant on paper (`paper_mm`). Run commands from the `sketch2cad-web/` folder.

Implements: REQ-09, REQ-05

## Acceptance Criteria

- [ ] The picker lists exactly the symbols returned by the catalog endpoint (8 in the default, asserted in `SymbolPicker.test.tsx`).
- [ ] Placing `evidence_marker` without `evidence_number` is blocked with a Spanish validation message.
- [ ] A `world_m` symbol's rendered width scales by 2 when zoom doubles; a `paper_mm` symbol's does not (unit test on `symbols.ts`).
