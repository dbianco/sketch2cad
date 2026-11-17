---
id: 001-087
title: "Build the PDF sheet: frame, title block, scale selection"
status: in_review
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-082]
implements: [REQ-13]
files: ["sketch2cad-api/src/sketch2cad/export/pdf.py", "sketch2cad-api/src/sketch2cad/export/pdf_sheet.py", "sketch2cad-api/src/sketch2cad/export/scale.py", "sketch2cad-api/tests/export/test_pdf_layout.py"]
parallelizable: false
estimate: { optimistic: 8, likely: 14, pessimistic: 24 }
labels: [ai-sdlc, python]
---

## Description

ReportLab with `invariant=1`, A4 or A3 landscape. `scale.py` proposes the largest of 1:50, 1:100, 1:200, 1:250, 1:500 where the plan fits with a 15 mm margin; never fit-to-page. Title block: code, title, incident type, date, technician, reviewer, revision, `metric_status`, short `snapshot_sha256`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-13

## Acceptance Criteria

- [ ] A plan of 20 × 12 m on A3 proposes 1:100 (`tests/export/test_pdf_layout.py`).
- [ ] A 1 m line at 1:100 measures 10 mm ± 0.05 mm (28.35 pt) in the PDF, measured by extracting the vector with pypdfium2 or pdfplumber (H4-3).
- [ ] Two generations of the same input have equal SHA-256 (CA-08).
