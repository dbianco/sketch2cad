---
id: 001-066
title: "Add the lease banner and the review transition UI"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-060, 001-058]
implements: [REQ-05, REQ-10, US-14, V-12, V-10, V-09]
files: ["sketch2cad-web/src/features/plans/LeaseBanner.tsx", "sketch2cad-web/src/features/plans/TransitionBar.tsx", "sketch2cad-web/src/features/plans/ReviewPage.tsx", "sketch2cad-web/src/features/plans/ReviewPage.test.tsx"]
parallelizable: false
estimate: { optimistic: 8, likely: 13, pessimistic: 22 }
labels: [ai-sdlc, typescript]
---

## Description

Banner shows the lease holder and switches the editor to read-only when another user holds it (V-12 text). Review page follows prototype screen 5 with Validate and Reject; reject requires the reason (V-09). Validated plans show the V-10 message and a Reopen button for reviewers. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05, REQ-10, US-14, V-12, V-10, V-09

## Acceptance Criteria

- [ ] `pnpm test --run src/features/plans/ReviewPage.test.tsx` passes: Reject without reason disables submit; a technician does not see Validate.
- [ ] With a 409 lease response, the editor toolbar buttons are disabled and the banner reads "{usuario} está editando este plano. Entra en solo lectura o espera a que termine.".
