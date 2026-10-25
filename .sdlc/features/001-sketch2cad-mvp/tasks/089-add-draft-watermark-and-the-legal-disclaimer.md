---
id: 001-089
title: "Add draft watermark and the legal disclaimer"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-088, 001-086]
implements: [REQ-13, REQ-14, US-15]
files: ["sketch2cad-api/src/sketch2cad/export/pdf_watermark.py", "sketch2cad-api/src/sketch2cad/export/disclaimer.py", "sketch2cad-api/src/sketch2cad/export/pdf.py", "sketch2cad-api/src/sketch2cad/export/dxf.py", "sketch2cad-api/tests/export/test_draft_disclaimer.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 6, pessimistic: 10 }
labels: [ai-sdlc, python]
---

## Description

Draft exports show the diagonal `BORRADOR / DRAFT` mark in the PDF and the `DRAFT — NOT VALIDATED` text in the DXF and omit the hash table. Final PDF and audit report print "Borrador técnico, no apto como prueba hasta contar con revisión jurídica" while `legal.disclaimer_enabled` is true (default). Run commands from the `sketch2cad-api/` folder.

Implements: REQ-13, REQ-14, US-15

## Acceptance Criteria

- [ ] The draft PDF text contains "DRAFT" and no hash table; the final contains the disclaimer sentence (H4-7, `tests/export/test_draft_disclaimer.py`).
- [ ] Setting `legal.disclaimer_enabled=false` removes the sentence.
