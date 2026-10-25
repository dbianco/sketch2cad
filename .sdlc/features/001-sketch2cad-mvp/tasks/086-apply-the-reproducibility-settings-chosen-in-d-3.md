---
id: 001-086
title: "Apply the reproducibility settings chosen in D-3"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-085]
implements: [REQ-12]
files: ["sketch2cad-api/src/sketch2cad/export/dxf_repro.py", "sketch2cad-api/src/sketch2cad/export/dxf.py", "sketch2cad-api/tests/export/test_dxf_repro.py"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 9 }
labels: [ai-sdlc, python]
---

## Description

Fix `$TDCREATE`, `$TDUPDATE`, `$FINGERPRINTGUID`, `$VERSIONGUID` and handle allocation as documented in `docs/spikes/dxf-reproducibility.md` (derive them from the `snapshot_sha256`). Run commands from the `sketch2cad-api/` folder.

Implements: REQ-12

## Acceptance Criteria

- [ ] Two exports of the same revision in separate processes 2 seconds apart have the same SHA-256, or the same canonical content as stated in the D-3 decision (H4-4, `tests/export/test_dxf_repro.py`).
- [ ] Exporting a different revision yields a different hash.
