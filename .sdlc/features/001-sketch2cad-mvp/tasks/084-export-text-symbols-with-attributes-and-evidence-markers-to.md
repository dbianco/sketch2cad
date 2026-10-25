---
id: 001-084
title: "Export text, symbols with attributes and evidence markers to DXF"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-083]
implements: [REQ-12]
files: ["sketch2cad-api/src/sketch2cad/export/dxf_text.py", "sketch2cad-api/src/sketch2cad/export/dxf_symbols.py", "sketch2cad-api/src/sketch2cad/export/dxf.py", "sketch2cad-api/tests/export/test_dxf_symbols.py"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, python]
---

## Description

`text → TEXT` with style `S2C_TEXT` (height = `height_mm_paper × paper_scale / 1000`); each catalog symbol becomes block `S2C_<symbol_id>` and entities become `INSERT` with `ATTRIB`s; anotative symbols scale by `paper_mm × paper_scale / 1000`; evidence points add the marker `INSERT`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-12

## Acceptance Criteria

- [ ] A symbol with `evidence_number = 3` appears as an `INSERT` of `S2C_evidence_marker` with an `ATTRIB` `evidence_number` equal to "3" (`tests/export/test_dxf_symbols.py`).
- [ ] A `paper_mm` symbol of 6 mm at 1:100 has scale 0.6; at 1:50 it has 0.3.
- [ ] `doc.audit()` reports 0 errors.
