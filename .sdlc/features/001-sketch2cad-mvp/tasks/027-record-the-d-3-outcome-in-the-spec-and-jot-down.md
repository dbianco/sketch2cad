---
id: 001-027
title: "Record the D-3 outcome in the spec and Jot Down"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-026, 001-025]
implements: [REQ-16, REQ-12]
files: ["docs/specs/Sketch2CAD_MVP.md", ".sdlc/features/001-sketch2cad-mvp/jotdown.md"]
parallelizable: false
estimate: { optimistic: 1, likely: 1, pessimistic: 2 }
labels: [ai-sdlc, docs]
---

## Description

Replace `PENDING DECISION` for D-3 with the chosen approach and add a one-line result for the SVG spike in the architectural decisions table. Update criterion H4-4 to match the chosen reproducibility level.

Implements: REQ-16, REQ-12

## Acceptance Criteria

- [ ] `rg -n 'PENDING DECISION' .sdlc/features/001-sketch2cad-mvp/jotdown.md` returns no matches.
- [ ] Criterion H4-4 in the Jot Down names either byte-identical hashes or canonical-content equality, consistent with `docs/spikes/dxf-reproducibility.md`.
