---
id: 001-095
title: "Document and automate the DXF viewer checks"
status: in_review
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-093]
implements: [REQ-12]
files: ["docs/dxf-viewers.md", "sketch2cad-api/scripts/open_dxf_xvfb.sh", ".github/workflows/dxf-viewers.yml"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 9 }
labels: [ai-sdlc, python]
---

## Description

Manual FreeCAD check (checklist plus screenshot attached to the PR for each milestone) and an optional CI job that opens the golden DXF in LibreCAD or QCAD under `xvfb`; if the headless open is not viable, document it and keep the job disabled with the reason (H4-2). Run commands from the `sketch2cad-api/` folder.

Implements: REQ-12

## Acceptance Criteria

- [ ] `docs/dxf-viewers.md` contains the FreeCAD checklist and a dated result with the FreeCAD version.
- [ ] `bash scripts/open_dxf_xvfb.sh tests/golden/plan.dxf` exits 0 where LibreCAD is installed, or the document states why the job is disabled.
