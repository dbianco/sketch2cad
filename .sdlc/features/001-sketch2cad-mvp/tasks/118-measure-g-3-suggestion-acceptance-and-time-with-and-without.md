---
id: 001-118
title: "Measure G-3: suggestion acceptance and time with and without suggestions"
status: todo
kind: acceptance-run
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-116]
implements: [REQ-20, REQ-17]
files: ["validation/suggestions.csv"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, docs]
---

## Description

Query `entity.reviewed` events for the 10 sketches to compute the share of polylines accepted unchanged and time two conversions per sketch (with and without suggestions). Skipped and declared as pending if H5 was cut.

Implements: REQ-20, REQ-17

## Acceptance Criteria

- [ ] `suggestions.csv` reports accepted-unchanged percentage and the two times per sketch.
- [ ] If H5 was not built the file states "H5 no construido" and the report marks CA-04, CA-05 and G-3 as validación posterior.
