---
id: 001-025
title: "Run spike 1: SVG editor performance with 2000 entities"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-002, 001-017]
implements: [REQ-05, REQ-16]
files: ["sketch2cad-web/spikes/svg-perf/index.html", "sketch2cad-web/spikes/svg-perf/run.ts", "sketch2cad-web/spikes/svg-perf/result.json", "docs/spikes/svg-performance.md", "sketch2cad-web/package.json"]
parallelizable: false
estimate: { optimistic: 4, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, typescript]
---

## Description

Generate 2000 synthetic entities (1200 polylines of 4 to 12 vertices, 600 lines, 200 symbols) in an `<svg>`, run a 10 second programmatic pan and zoom, record `median_fps`, `p95_frame_ms`, browser and hardware in `result.json`, and write the decision in `docs/spikes/svg-performance.md` (decision D-2). Run commands from the `sketch2cad-web/` folder.

Implements: REQ-05, REQ-16

## Acceptance Criteria

- [ ] `pnpm spike:svg` writes `spikes/svg-perf/result.json` with the keys `median_fps`, `p95_frame_ms`, `entities` (value 2000), `browser`, `hardware`.
- [ ] The decision document states keep SVG when `median_fps >= 60` and `p95_frame_ms <= 25`, otherwise it recommends Konva and states that H2 does not start (criterion H0-10).
- [ ] `jq '.entities' spikes/svg-perf/result.json` prints 2000.
