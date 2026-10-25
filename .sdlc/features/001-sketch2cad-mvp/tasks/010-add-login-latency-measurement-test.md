---
id: 001-010
title: "Add login latency measurement test"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-007]
implements: [REQ-01, REQ-16]
files: ["sketch2cad-api/tests/perf/test_login_latency.py"]
parallelizable: false
estimate: { optimistic: 1, likely: 2, pessimistic: 3 }
labels: [ai-sdlc, python]
---

## Description

Measures 20 consecutive valid logins and computes p95. Fails locally above 300 ms (criterion H0-2); in CI (`CI=true`) it records the value in the job summary and does not fail. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01, REQ-16

## Acceptance Criteria

- [ ] `uv run pytest tests/perf/test_login_latency.py -q` on the development machine fails if p95 ≥ 300 ms and passes below it.
- [ ] With `CI=true` the test never fails on latency and writes `login_p95_ms=<value>` to `$GITHUB_STEP_SUMMARY` when that variable is set.
