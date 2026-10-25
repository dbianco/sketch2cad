---
id: 001-103
title: "Implement bulk review and metric-origin promotion"
status: todo
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-102, 001-076]
implements: [REQ-17, US-12, REQ-10]
files: ["sketch2cad-api/src/sketch2cad/entities/bulk_review.py", "sketch2cad-api/src/sketch2cad/api/routers/entities.py", "sketch2cad-api/src/sketch2cad/geometry/metric_status.py", "sketch2cad-api/tests/entities/test_bulk_review.py"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 18 }
labels: [ai-sdlc, python]
---

## Description

`POST /revisions/{id}/entities:bulk-review {ids, review_status}` updates all in one transaction and writes one `entity.reviewed` per entity; if any id belongs to another revision, nothing changes. Promotion rule: accepted or corrected entities in `scaled_drawing` with metric calibration become `scaled_drawing`; in `schematic_sketch` they stay `trace_only`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-17, US-12, REQ-10

## Acceptance Criteria

- [ ] Reviewing 100 ids changes 100 rows and creates 100 events; with one foreign id it changes none (H5-4, `tests/entities/test_bulk_review.py`).
- [ ] In `scaled_drawing`, a suggestion is `trace_only` while `suggested` and `scaled_drawing` after acceptance; in `schematic_sketch` it stays `trace_only` after acceptance (H5-10).
- [ ] A test of the invariant: no entity with `source.method != 'manual'` is `accepted` without an `entity.reviewed` event (H5-8).
