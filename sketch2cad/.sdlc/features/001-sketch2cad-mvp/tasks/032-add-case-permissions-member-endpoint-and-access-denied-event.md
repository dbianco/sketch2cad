---
id: 001-032
title: "Add case permissions, member endpoint and access.denied events"
status: in_review
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-030, 001-012]
implements: [REQ-01, US-19]
files: ["sketch2cad-api/src/sketch2cad/cases/permissions.py", "sketch2cad-api/src/sketch2cad/api/deps.py", "sketch2cad-api/src/sketch2cad/api/routers/cases.py", "sketch2cad-api/tests/cases/test_permissions.py"]
parallelizable: false
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

`require_case_permission("read"|"edit"|"deliver")` dependency; `POST /case-files/{id}/members` (creator or admin) with permissions list; every denial writes an `access.denied` event with actor, route and case id. Retrofit `case_file.created` and `member.added` events into the existing creation code. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01, US-19

## Acceptance Criteria

- [ ] With two users, the non-member receives 403 or 404 on read, edit and download routes and each attempt creates one `access.denied` event (`tests/cases/test_permissions.py`).
- [ ] Adding a member with permissions `["read"]` allows reads and denies `edit` (403).
- [ ] Creating a case writes `case_file.created`; adding a member writes `member.added`.
