---
id: 001-085
title: "Export dimensions to DXF with declared value override"
status: in_review
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-084]
implements: [REQ-12]
files: ["sketch2cad-api/src/sketch2cad/export/dxf_dimensions.py", "sketch2cad-api/src/sketch2cad/export/dxf.py", "sketch2cad-api/tests/export/test_dxf_dimensions.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

Aligned `DIMENSION` entities rendered with `render()`, text overridden with the declared value (comma-free, two decimals) when present; entities with `discrepancy = true` use an alert style. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-12

## Acceptance Criteria

- [ ] A dimension declared 4.20 m shows text "4.20" in the DXF while its geometric measurement is its real length (`tests/export/test_dxf_dimensions.py`).
- [ ] `doc.audit()` reports 0 errors with dimensions present.
