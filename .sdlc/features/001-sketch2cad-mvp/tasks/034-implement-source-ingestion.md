---
id: 001-034
title: "Implement source ingestion"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-029, 001-032, 001-028]
implements: [REQ-02, US-02, V-01, V-02, V-03]
files: ["sketch2cad-api/src/sketch2cad/sources/__init__.py", "sketch2cad-api/src/sketch2cad/sources/ingest.py", "sketch2cad-api/src/sketch2cad/sources/magic.py", "sketch2cad-api/src/sketch2cad/api/routers/sources.py", "sketch2cad-api/tests/sources/test_ingest.py"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, python]
---

## Description

`POST /api/v1/case-files/{id}/sources` multipart with `file` and `client_sha256`. Validate type by magic bytes (JPEG, PNG, TIFF, single-page PDF), size ≤ 50 MB (413), compare server and client hashes (422 with the V-03 message and no stored object), store via `Storage`, read width/height/DPI/EXIF with Pillow, insert `source` and `source.ingested`, then enqueue `qc`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-02, US-02, V-01, V-02, V-03

## Acceptance Criteria

- [ ] A 50 MB + 1 byte upload returns 413; a `.jpg` containing text returns 415 (`tests/sources/test_ingest.py`).
- [ ] A mismatching `client_sha256` returns 422 with detail "El archivo se alteró durante la subida. Vuelve a cargarlo." and the objects directory is empty afterwards.
- [ ] A valid upload returns 201 with `sha256` equal to `hashlib.sha256(bytes)` and creates exactly one `source.ingested` event.
- [ ] A two-page PDF returns 415.
