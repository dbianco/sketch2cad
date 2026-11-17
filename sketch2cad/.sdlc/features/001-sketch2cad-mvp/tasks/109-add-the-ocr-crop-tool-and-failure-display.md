---
id: 001-109
title: "Add the OCR crop tool and failure display"
status: in_review
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-107, 001-060]
implements: [US-13, REQ-18, REQ-15, US-18]
files: ["sketch2cad-web/src/editor/tools/OcrCropTool.tsx", "sketch2cad-web/src/editor/tools/OcrCropTool.test.tsx", "sketch2cad-web/src/features/jobs/JobFailure.tsx"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, typescript]
---

## Description

Draw a box on the source, poll the job, show the proposed value and confidence, accept or type the value, associate it to the selected segment or dimension. Failed jobs show `cause` and `action` and leave the plan editable. Run commands from the `sketch2cad-web/` folder.

Implements: US-13, REQ-18, REQ-15, US-18

## Acceptance Criteria

- [ ] `pnpm test --run src/editor/tools/OcrCropTool.test.tsx` passes: a result with confidence 0.82 preselects the value; below 0.6 nothing is preselected.
- [ ] A failed job renders the cause and action text and the editor toolbar stays enabled (H5-6).
