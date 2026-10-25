---
id: 001-021
title: "Add the license allow-list check for the web repository"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-002]
implements: [INFRA, REQ-16]
files: ["sketch2cad-web/licenses-allowed.json", "sketch2cad-web/license-exceptions.json", "sketch2cad-web/scripts/check-licenses.mjs", "sketch2cad-web/scripts/check-licenses.test.mjs"]
parallelizable: false
estimate: { optimistic: 2, likely: 4, pessimistic: 6 }
labels: [ai-sdlc, typescript]
---

## Description

Uses `license-checker --json`; same allow-list and exception semantics as the API repository. Run commands from the `sketch2cad-web/` folder.

Implements: INFRA, REQ-16

## Acceptance Criteria

- [ ] `node scripts/check-licenses.mjs` exits 0 on the current lockfile.
- [ ] `node --test scripts/check-licenses.test.mjs` passes, including a synthetic GPL-3.0 package that exits non-zero naming the package.
