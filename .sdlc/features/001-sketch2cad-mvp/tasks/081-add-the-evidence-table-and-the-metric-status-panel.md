---
id: 001-081
title: "Add the evidence table and the metric status panel"
status: todo
milestone: "H3 — Herramientas métricas"
repository: sketch2cad
depends_on: [001-078]
implements: [US-10, US-11, REQ-08, REQ-07]
files: ["sketch2cad-web/src/features/evidence/EvidenceTable.tsx", "sketch2cad-web/src/features/plans/MetricStatusPanel.tsx", "sketch2cad-web/src/features/plans/MetricStatusPanel.test.tsx", "sketch2cad-web/src/features/evidence/EvidenceTable.test.tsx"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, typescript]
---

## Description

Evidence table with number, label, local X, Y and metric origin, linked to the entities. The status badge in the editor header opens a panel listing the entities that block the `metric` state; clicking one selects it on the canvas. Run commands from the `sketch2cad-web/` folder.

Implements: US-10, US-11, REQ-08, REQ-07

## Acceptance Criteria

- [ ] `pnpm test --run src/features/plans/MetricStatusPanel.test.tsx` passes: three blocking entities render three list items and clicking the second selects its id.
- [ ] The evidence table shows coordinates with two decimals and the comma as decimal separator.
