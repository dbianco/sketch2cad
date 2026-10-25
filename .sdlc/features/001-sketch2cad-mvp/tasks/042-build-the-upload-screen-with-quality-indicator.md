---
id: 001-042
title: "Build the upload screen with quality indicator"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-041]
implements: [US-02, REQ-02, V-01, V-02, V-03]
files: ["sketch2cad-web/src/features/sources/UploadPage.tsx", "sketch2cad-web/src/features/sources/hashFile.ts", "sketch2cad-web/src/features/sources/QcPanel.tsx", "sketch2cad-web/src/features/sources/UploadPage.test.tsx"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, typescript]
---

## Description

Follows prototype screen 2. Computes SHA-256 in the browser (Web Crypto, streaming by chunks), uploads with `client_sha256`, polls `GET /jobs/{id}` every 2 s, shows the traffic-light QC and its reasons. Errors 413, 415 and 422 show the messages from V-01, V-02, V-03. Run commands from the `sketch2cad-web/` folder.

Implements: US-02, REQ-02, V-01, V-02, V-03

## Acceptance Criteria

- [ ] `pnpm test --run src/features/sources/UploadPage.test.tsx` passes: hash of a known byte array equals its known SHA-256; 413, 415 and 422 each render their message.
- [ ] Polling stops when the job reaches `succeeded` or `failed` (asserted with fake timers: no request after the terminal state).
- [ ] The QC amber state shows its reason text and the continue button stays enabled.
