---
id: 001-068
title: "Measure the real editor with 2000 entities"
status: in_review
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-062]
implements: [REQ-05]
files: ["sketch2cad-web/spikes/svg-perf/editor-run.ts", "sketch2cad-web/spikes/svg-perf/editor-result.json", "docs/spikes/editor-performance.md"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 9 }
labels: [ai-sdlc, typescript]
---

## Description

Reuse the spike harness against the real `EditorCanvas` with 2000 entities (H2-12). Runs on the development laptop; CI only checks that the script executes. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05

## Acceptance Criteria

- [ ] `pnpm spike:editor` writes `editor-result.json` with `median_fps >= 60` and `p95_frame_ms <= 25`, or the report recommends Konva and blocks H3.
- [ ] The report lists hardware and browser.
