---
id: 001-023
title: "Add GitHub Actions CI for the web repository"
status: partial
unmet: ["contract job fails on a hand-edited schema.d.ts in a real run, run URL recorded (simulated locally, needs GitHub)", "audit job fails on a vulnerable dependency in a real run (simulated locally with lodash 4.17.11, needs GitHub)", "run summary shows ci_duration_minutes in a real workflow run (needs GitHub)"]
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-021, 001-017, 001-015]
implements: [INFRA, REQ-16]
files: [".github/workflows/web-ci.yml", "sketch2cad-web/audit-exceptions.json", "sketch2cad-web/.gitleaks.toml"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 8 }
labels: [ai-sdlc, typescript]
---

## Description

Workflow `web-ci.yml` at the repository root with `paths: sketch2cad-web/**` and `working-directory: sketch2cad-web`. Jobs: lint (eslint, tsc), unit (vitest), contract (`scripts/generate-client.sh` + `git diff --exit-code`), licenses, audit (`pnpm audit --audit-level=high` filtered by `audit-exceptions.json`), secrets (gitleaks) and build. Duration reported in the job summary. Run commands from the `sketch2cad-web/` folder.

Implements: INFRA, REQ-16

## Acceptance Criteria

- [ ] A branch that edits `src/api/schema.d.ts` by hand makes the `contract` job fail (verified once; run URL recorded in the PR).
- [ ] A branch adding a dependency with a known high-severity advisory and no exception makes the `audit` job fail (verified once).
- [ ] The run summary contains `ci_duration_minutes=<n>`.
