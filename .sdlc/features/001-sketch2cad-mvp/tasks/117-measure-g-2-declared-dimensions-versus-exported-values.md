---
id: 001-117
title: "Measure G-2: declared dimensions versus exported values"
status: todo
kind: acceptance-run
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-116]
implements: [REQ-20]
files: ["validation/dimension_check.csv"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, docs]
---

## Description

A second person compares every declared dimension written on each sketch with the value in the exported DXF.

Implements: REQ-20

## Acceptance Criteria

- [ ] `dimension_check.csv` lists every dimension with written value, exported value and match flag; the match percentage is computed and reported (target ≥ 99 %).
- [ ] The second reviewer's name or initials appear in the file header.
