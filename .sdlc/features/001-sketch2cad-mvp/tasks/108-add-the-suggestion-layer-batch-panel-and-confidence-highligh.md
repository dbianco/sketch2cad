---
id: 001-108
title: "Add the suggestion layer, batch panel and confidence highlight"
status: in_review
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-107, 001-069]
implements: [US-12, REQ-17]
files: ["sketch2cad-web/src/features/suggestions/SuggestionLayer.tsx", "sketch2cad-web/src/features/suggestions/BatchPanel.tsx", "sketch2cad-web/src/features/suggestions/BatchPanel.test.tsx"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, typescript]
---

## Description

Suggested polylines in a distinct color; entities below `suggestions.min_confidence_highlight` (0.7) highlighted; panel with accept all, reject all and per-item actions; counts by confidence. Hidden when the AI switch is off. Run commands from the `sketch2cad-web/` folder.

Implements: US-12, REQ-17

## Acceptance Criteria

- [ ] `pnpm test --run src/features/suggestions/BatchPanel.test.tsx` passes: accept-all sends one bulk request with every id; entities with confidence 0.5 carry the highlight class.
- [ ] With the switch off none of the suggestion controls render.
