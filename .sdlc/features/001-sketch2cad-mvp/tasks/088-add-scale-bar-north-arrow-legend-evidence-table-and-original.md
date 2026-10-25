---
id: 001-088
title: "Add scale bar, north arrow, legend, evidence table and original annex"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-087]
implements: [REQ-13]
files: ["sketch2cad-api/src/sketch2cad/export/pdf_legend.py", "sketch2cad-api/src/sketch2cad/export/pdf_evidence.py", "sketch2cad-api/src/sketch2cad/export/pdf_annex.py", "sketch2cad-api/src/sketch2cad/export/pdf.py", "sketch2cad-api/tests/export/test_pdf_content.py"]
parallelizable: false
estimate: { optimistic: 8, likely: 14, pessimistic: 24 }
labels: [ai-sdlc, python]
---

## Description

Numeric and graphic scale, north arrow from the local frame, legend of used symbols, evidence table (number, label, X, Y, metric origin), and an annex page with the original (or rectified) image and its hash. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-13

## Acceptance Criteria

- [ ] The extracted text of the PDF contains every evidence number and label of the fixture and the annex hash (`tests/export/test_pdf_content.py`).
- [ ] The graphic scale bar of 5 m at 1:100 measures 50 mm ± 0.05 mm.
- [ ] The PDF has exactly one annex page.
