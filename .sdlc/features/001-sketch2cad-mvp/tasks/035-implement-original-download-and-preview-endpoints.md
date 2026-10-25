---
id: 001-035
title: "Implement original download and preview endpoints"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-034]
implements: [REQ-02, US-02, REQ-01]
files: ["sketch2cad-api/src/sketch2cad/sources/preview.py", "sketch2cad-api/src/sketch2cad/api/routers/sources.py", "sketch2cad-api/tests/sources/test_download_preview.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 6, pessimistic: 10 }
labels: [ai-sdlc, python]
---

## Description

`GET /sources/{id}/original` streams the stored bytes with header `X-Content-SHA256` and writes `source.downloaded`; `GET /sources/{id}/preview?max=2048` returns a derived JPEG whose longest side ≤ `max` (256 to 4096, else 422). Both require `read` permission. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-02, US-02, REQ-01

## Acceptance Criteria

- [ ] Upload then download returns bytes whose SHA-256 equals the registered hash and the header `X-Content-SHA256` (CA-01, H1-1) in `tests/sources/test_download_preview.py`.
- [ ] Preview with `max=512` returns an image whose `max(width, height) <= 512`; `max=100` returns 422.
- [ ] A non-member receives 403 and an `access.denied` event.
