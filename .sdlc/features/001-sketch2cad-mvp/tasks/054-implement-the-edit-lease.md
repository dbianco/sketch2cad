---
id: 001-054
title: "Implement the edit lease"
status: in_review
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-052, 001-053]
implements: [REQ-05, V-12]
files: ["sketch2cad-api/src/sketch2cad/plans/lease.py", "sketch2cad-api/src/sketch2cad/api/routers/plans.py", "sketch2cad-api/src/sketch2cad/entities/service.py", "sketch2cad-api/tests/plans/test_lease.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

`POST /plans/{id}/lease` acquires or renews (10 minutes); `DELETE` releases; a second user gets 409 with `holder` and the V-12 message. Entity mutations require the caller to hold the lease. Events `lease.acquired` and `lease.released`. Injectable clock. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-05, V-12

## Acceptance Criteria

- [ ] A second user requesting the lease while it is held receives 409 containing the holder's display name (H2-9, `tests/plans/test_lease.py`).
- [ ] With the clock advanced 11 minutes without renewal, the second user obtains the lease.
- [ ] An entity PATCH by a user without the lease returns 409.
