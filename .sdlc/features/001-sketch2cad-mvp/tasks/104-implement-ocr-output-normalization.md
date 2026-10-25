---
id: 001-104
title: "Implement OCR output normalization"
status: todo
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-099]
implements: [REQ-18]
files: ["sketch2cad-api/src/sketch2cad/ocr/__init__.py", "sketch2cad-api/src/sketch2cad/ocr/normalize.py", "sketch2cad-api/tests/ocr/test_normalize.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 3, pessimistic: 5 }
labels: [ai-sdlc, python]
---

## Description

Normalize "3,50", "3.50 m", "3,50mts" to 3.5; return `value_m = None` for non-numeric text; apply `ocr.min_confidence` (default 0.6) for preselection. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-18

## Acceptance Criteria

- [ ] 10 table-driven cases pass, including the three above (H5-5, `tests/ocr/test_normalize.py`).
- [ ] Text "abc" returns `value_m is None`.
