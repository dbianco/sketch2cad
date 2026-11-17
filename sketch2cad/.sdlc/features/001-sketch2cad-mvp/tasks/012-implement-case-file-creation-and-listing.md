---
id: 001-012
title: "Implement case file creation and listing"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-009, 001-004]
implements: [REQ-01, US-01]
files: ["sketch2cad-api/src/sketch2cad/cases/__init__.py", "sketch2cad-api/src/sketch2cad/cases/service.py", "sketch2cad-api/src/sketch2cad/api/routers/cases.py", "sketch2cad-api/tests/cases/test_case_files.py"]
parallelizable: false
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

`POST /api/v1/case-files` creates a case with `code` `EXP-<year>-<NNNN>` from the per-year sequence and adds the creator as member with all permissions. `GET /api/v1/case-files?q=&incident_type=` lists cases where the user is creator or member. `GET /api/v1/case-files/{id}` returns 404 for non-members (indistinguishable from missing). Covers H0-4. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01, US-01

## Acceptance Criteria

- [ ] Two concurrent creations (two threads) return two distinct codes matching `^EXP-\d{4}-\d{4}$` (`tests/cases/test_case_files.py`).
- [ ] A user who is not a member receives 404 for `GET /case-files/{id}` and the case is absent from their list.
- [ ] `POST /case-files` with `incident_type="other"` and no `description` returns 422 (description required).
