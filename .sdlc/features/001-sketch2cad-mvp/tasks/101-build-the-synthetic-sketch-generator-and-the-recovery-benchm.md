---
id: 001-101
title: "Build the synthetic sketch generator and the recovery benchmark"
status: in_review
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-100]
implements: [REQ-17]
files: ["sketch2cad-api/src/sketch2cad/vision/synthetic_sketch.py", "sketch2cad-api/tests/vision/test_recovery_benchmark.py", "sketch2cad-api/tests/vision/test_performance.py"]
parallelizable: false
estimate: { optimistic: 8, likely: 14, pessimistic: 24 }
labels: [ai-sdlc, python]
---

## Description

Generator draws N known segments with Gaussian jitter, variable thickness and paper noise; the benchmark compares the pipeline output with the truth using shapely. The performance test times a 4000 × 3000 px image. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-17

## Acceptance Criteria

- [ ] Over at least 20 generated images, ≥ 90 % of segments longer than 20 mm paper are recovered with vertex deviation < 2 mm paper, and spurious polylines under 5 mm number 0 (CA-04, H5-1).
- [ ] Processing a 4000 × 3000 px image takes under 30 seconds on 4 CPU cores (H5-2); the test records the CPU count and fails if it is below 4 cores only when `CI` is unset.
