---
id: 001-114
title: "Final license and dependency audit (CA-13)"
status: in_review
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-110, 001-098]
implements: [REQ-16, INFRA]
files: ["docs/dependency-audit.md", "sketch2cad-api/license-exceptions.toml"]
parallelizable: true
estimate: { optimistic: 2, likely: 3, pessimistic: 5 }
labels: [ai-sdlc, python]
---

## Description

Run both license checks across API and web with the final lockfiles, include the transitive dependencies of torch, transformers and opencv, and document each exception. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-16, INFRA

## Acceptance Criteria

- [ ] `uv run python scripts/check_licenses.py` and `node scripts/check-licenses.mjs` exit 0 (H6-4).
- [ ] Every entry of `license-exceptions.toml` has package, version and non-empty reason.
