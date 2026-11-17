---
id: 001-116
title: "Measure G-1: time against FreeCAD on at least 10 sketches"
status: todo
kind: acceptance-run
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-115, 001-113]
implements: [REQ-20]
files: ["validation/timings.csv", "validation/measure_protocol.md"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, docs]
---

## Description

Time converting each sketch with Sketch2CAD and with FreeCAD, same plan, same person; record in `timings.csv` with the method. The result is a measured baseline, not a threshold pass or fail.

Implements: REQ-20

## Acceptance Criteria

- [ ] `timings.csv` has at least 10 rows with both times in minutes and the date (H6-6).
- [ ] `validation/measure_protocol.md` states how time is counted (start at opening the sketch, stop at the exported DXF).
