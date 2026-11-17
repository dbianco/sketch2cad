---
id: 001-098
title: "Add the E2E: validate, export and verify the package"
status: in_review
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-097, 001-092]
implements: [US-16, US-17, REQ-14]
files: ["sketch2cad-web/e2e/export_verify.spec.ts", "sketch2cad-web/scripts/verify_downloaded_zip.sh"]
parallelizable: false
estimate: { optimistic: 5, likely: 9, pessimistic: 16 }
labels: [ai-sdlc, typescript]
---

## Description

Validate the sample plan as reviewer, run the final export, download the ZIP and run `sketch2cad-verify` on it inside the test (H4-10). Run commands from the `sketch2cad-web/` folder.

Implements: US-16, US-17, REQ-14

## Acceptance Criteria

- [ ] `pnpm exec playwright test e2e/export_verify.spec.ts` passes against the full stack with the verifier exiting 0.
- [ ] The same test, after flipping one byte in the downloaded ZIP, expects the verifier to exit 1.
