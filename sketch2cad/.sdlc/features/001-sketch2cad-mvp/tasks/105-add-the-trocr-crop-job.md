---
id: 001-105
title: "Add the TrOCR crop job"
status: in_review
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-104, 001-102]
implements: [REQ-18, US-13]
files: ["sketch2cad-api/src/sketch2cad/ocr/trocr.py", "sketch2cad-api/src/sketch2cad/worker/tasks_ocr.py", "sketch2cad-api/Dockerfile.worker", "sketch2cad-api/tests/ocr/test_ocr_job.py"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, python]
---

## Description

Model `microsoft/trocr-base-handwritten` loaded from the image layer (downloaded at build, never at runtime). `ocr_crop {source_id, box_px}` returns `{text, value_m, confidence}`. Logs rate of acceptance when the user confirms (`entity.reviewed` with `source.method = ocr`). Run commands from the `sketch2cad-api/` folder.

Implements: REQ-18, US-13

## Acceptance Criteria

- [ ] A crop of a rendered "3,50" returns `value_m == 3.5` in under 3 seconds on CPU (`tests/ocr/test_ocr_job.py`, marked `slow`).
- [ ] The worker starts with no network access (`docker run --network none`) and still serves the OCR job (model baked into the image).
- [ ] A crop outside the image bounds returns 422.
