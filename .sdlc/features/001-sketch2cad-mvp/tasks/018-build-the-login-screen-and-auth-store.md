---
id: 001-018
title: "Build the login screen and auth store"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-017]
implements: [REQ-01, US-19]
files: ["sketch2cad-web/src/features/auth/LoginPage.tsx", "sketch2cad-web/src/features/auth/authStore.ts", "sketch2cad-web/src/features/auth/LoginPage.test.tsx", "sketch2cad-web/src/app/routes.tsx"]
parallelizable: false
estimate: { optimistic: 4, likely: 6, pessimistic: 10 }
labels: [ai-sdlc, typescript]
---

## Description

Login form with labels bound to inputs, error message from the problem body, redirect to the case list after success, `GET /auth/me` on load, logout button. Spanish UI texts. Locked accounts (423) show the unlock time. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-01, US-19

## Acceptance Criteria

- [ ] `pnpm test --run src/features/auth/LoginPage.test.tsx` passes, covering success redirect, 401 message and 423 message with the time.
- [ ] Every input has an associated `<label>` (asserted with `getByLabelText`).
- [ ] `pnpm lint` exits 0.
