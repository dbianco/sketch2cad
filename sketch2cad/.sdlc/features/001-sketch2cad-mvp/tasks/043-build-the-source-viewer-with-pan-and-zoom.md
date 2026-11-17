---
id: 001-043
title: "Build the source viewer with pan and zoom"
status: in_review
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-042]
implements: [US-02, REQ-02]
files: ["sketch2cad-web/src/features/sources/SourceViewer.tsx", "sketch2cad-web/src/features/sources/SourceViewer.test.tsx"]
parallelizable: false
estimate: { optimistic: 4, likely: 6, pessimistic: 10 }
labels: [ai-sdlc, typescript]
---

## Description

Image viewer using `react-zoom-pan-pinch` loading `/sources/{id}/preview?max=2048`; zoom buttons with accessible labels and keyboard shortcuts (+, −, 0). Run commands from the `sketch2cad-web/` folder.

Implements: US-02, REQ-02

## Acceptance Criteria

- [ ] `pnpm test --run src/features/sources/SourceViewer.test.tsx` passes: the buttons have `aria-label` and pressing `+` increases the scale value exposed in the test hook.
- [ ] The viewer requests only the preview URL, never `/original` (asserted by the mocked fetch).
