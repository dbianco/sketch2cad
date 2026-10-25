---
id: 001-040
title: "Expose the event log endpoint"
status: in_review
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-032, 001-031]
implements: [REQ-11, REQ-01]
files: ["sketch2cad-api/src/sketch2cad/api/routers/events.py", "sketch2cad-api/tests/audit/test_events_endpoint.py"]
parallelizable: false
estimate: { optimistic: 3, likely: 4, pessimistic: 7 }
labels: [ai-sdlc, python]
---

## Description

`GET /api/v1/case-files/{id}/events?plan_id=&kind=&cursor=` returns paginated events with `prev_hash` and `hash`; requires `read`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-11, REQ-01

## Acceptance Criteria

- [ ] Returns events in id order with page size 50 and a `next_cursor` when more exist (`tests/audit/test_events_endpoint.py`).
- [ ] A non-member receives 403 and an `access.denied` event.
- [ ] Filtering by `kind=source.ingested` returns only events of that kind.
