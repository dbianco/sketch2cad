---
id: 001-110
title: "Add the E2E for suggestions and OCR"
status: todo
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-108, 001-109]
implements: [US-12, US-13, REQ-17, REQ-18]
files: ["sketch2cad-web/e2e/suggestions_ocr.spec.ts", "sketch2cad-web/e2e/fixtures/dimensioned_sketch.png"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, typescript]
---

## Description

Upload a synthetic sketch with a written dimension, accept calibration, review suggestions in bulk and use OCR on the dimension, with the AI switch on and off. Run commands from the `sketch2cad-web/` folder.

Implements: US-12, US-13, REQ-17, REQ-18

## Acceptance Criteria

- [ ] `pnpm exec playwright test e2e/suggestions_ocr.spec.ts` passes against the full stack.
- [ ] With `ai_assist.enabled=false` the same spec asserts the controls are absent (H5-7).
