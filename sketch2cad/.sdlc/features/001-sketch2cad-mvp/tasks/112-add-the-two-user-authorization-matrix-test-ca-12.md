---
id: 001-112
title: "Add the two-user authorization matrix test (CA-12)"
status: in_review
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-110, 001-098]
implements: [REQ-01, US-19, REQ-11]
files: ["sketch2cad-api/tests/security/test_authorization_matrix.py"]
parallelizable: true
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

For every route that takes a case, plan, source, entity, revision or artifact id, call it as a non-member and assert 403 or 404 plus an `access.denied` event. The route list is generated from `openapi.json` so new routes are covered automatically. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01, US-19, REQ-11

## Acceptance Criteria

- [ ] `uv run pytest tests/security/test_authorization_matrix.py -q` passes for 100 % of the routes with a path parameter (H6-3).
- [ ] Adding a route without permission checks makes the test fail (verified once with a temporary route).
