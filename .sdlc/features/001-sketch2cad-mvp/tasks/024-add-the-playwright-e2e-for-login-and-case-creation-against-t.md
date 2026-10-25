---
id: 001-024
title: "Add the Playwright E2E for login and case creation against the full stack"
status: partial
unmet: ["e2e GitHub workflow green on main and uploads the Playwright report (needs GitHub; actionlint passes)"]
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-022, 001-019]
implements: [REQ-16, REQ-01, US-01]
files: ["sketch2cad-web/e2e/login_case.spec.ts", "sketch2cad-web/e2e/global-setup.ts", "sketch2cad-web/playwright.config.ts", "sketch2cad-web/scripts/e2e-stack.sh", ".github/workflows/e2e.yml"]
parallelizable: false
estimate: { optimistic: 5, likely: 9, pessimistic: 16 }
labels: [ai-sdlc, typescript]
---

## Description

Decision D-9: the E2E lives in `sketch2cad-web/e2e/` and starts the compose of `sketch2cad-api/` from the same commit (`scripts/e2e-stack.sh` runs `docker compose up -d --wait` there and creates a user with the CLI). The test logs in, creates a case and sees it in the list. It must run against the real stack, never `vite preview`. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-16, REQ-01, US-01

## Acceptance Criteria

- [ ] `pnpm exec playwright test e2e/login_case.spec.ts` passes locally against the compose stack.
- [ ] `rg -n 'vite preview|pnpm preview' playwright.config.ts scripts/e2e-stack.sh .github/workflows/e2e.yml` returns no matches (the E2E never targets a static preview).
- [ ] The `e2e` GitHub workflow is green on `main` and uploads the Playwright report as an artifact.
