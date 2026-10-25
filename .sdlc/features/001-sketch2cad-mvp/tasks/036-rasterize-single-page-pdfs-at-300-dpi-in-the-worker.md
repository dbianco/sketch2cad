---
id: 001-036
title: "Rasterize single-page PDFs at 300 dpi in the worker"
status: in_review
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-034, 001-013]
implements: [REQ-02]
files: ["sketch2cad-api/src/sketch2cad/worker/tasks_raster.py", "sketch2cad-api/src/sketch2cad/sources/raster.py", "sketch2cad-api/tests/sources/test_raster.py", "sketch2cad-api/tests/fixtures/one_page.pdf"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 9 }
labels: [ai-sdlc, python]
---

## Description

pypdfium2 renders page 0 to a PNG stored as a derived object; the PDF is never served to the browser as a document. Width/height of the source are updated from the raster. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-02

## Acceptance Criteria

- [ ] Rasterizing `one_page.pdf` (A4) returns an image of 2480 x 3508 px ± 2 px (`tests/sources/test_raster.py`).
- [ ] `rg -n 'application/pdf' src/sketch2cad/api/routers/sources.py` shows no route returning a PDF to the browser except `original` with `Content-Disposition: attachment`.
