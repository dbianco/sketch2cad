---
id: 001-094
title: "Add the control-distance test across model, DXF and PDF"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-093]
implements: [REQ-13, REQ-12]
files: ["sketch2cad-api/tests/export/test_control_distances.py", "sketch2cad-api/tests/fixtures/control_plan.json"]
parallelizable: false
estimate: { optimistic: 4, likely: 6, pessimistic: 10 }
labels: [ai-sdlc, python]
---

## Description

Fixture with a horizontal, a vertical and a diagonal control segment; extract lengths from the model, DXF entities and PDF vectors and compare (CA-07). Run commands from the `sketch2cad-api/` folder.

Implements: REQ-13, REQ-12

## Acceptance Criteria

- [ ] `uv run pytest tests/export/test_control_distances.py -q` passes: the three distances agree within 0.5 % across model, DXF and PDF (H4-3).
- [ ] The test fails when the PDF scale is changed from 1:100 to 1:200 (verified once with a temporary patch, recorded in the PR).
