---
id: 001-064
title: "Build the calibration wizard (DPI, segment, 4 points)"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-058, 001-043]
implements: [US-03, REQ-03, V-05, US-04]
files: ["sketch2cad-web/src/features/calibration/CalibrationWizard.tsx", "sketch2cad-web/src/features/calibration/FourPointStep.tsx", "sketch2cad-web/src/features/calibration/ScanSteps.tsx", "sketch2cad-web/src/features/calibration/CalibrationWizard.test.tsx", "sketch2cad-web/src/features/calibration/messages.ts"]
parallelizable: false
estimate: { optimistic: 10, likely: 16, pessimistic: 28 }
labels: [ai-sdlc, typescript]
---

## Description

Follows prototype screen 3. Source-mode selector (default `schematic_sketch`), method selector, point marking on the source image, display of reprojection error (max, RMS), accept as metric (blocked with the V-05 message when above tolerance) or continue as approximate. Run commands from the `sketch2cad-web/` folder.

Implements: US-03, REQ-03, V-05, US-04

## Acceptance Criteria

- [ ] `pnpm test --run src/features/calibration/CalibrationWizard.test.tsx` passes: with a mocked error of 1.3 mm the accept-as-metric button is disabled and the V-05 message shows "1,3 mm" and "1,0 mm".
- [ ] Four clicks produce four control points with image-pixel coordinates in the request body.
- [ ] A photo cannot be marked metric without completing the four points (asserted).
