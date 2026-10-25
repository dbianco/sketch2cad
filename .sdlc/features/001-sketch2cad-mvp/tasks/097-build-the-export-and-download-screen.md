---
id: 001-097
title: "Build the export and download screen"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-096]
implements: [US-15, US-16, REQ-14, V-11]
files: ["sketch2cad-web/src/features/exports/ExportPage.tsx", "sketch2cad-web/src/features/exports/ArtifactTable.tsx", "sketch2cad-web/src/features/exports/ExportPage.test.tsx"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, typescript]
---

## Description

Follows prototype screen 6: draft and final buttons, paper and scale selectors (suggested scale preselected), job polling every 2 s, artifact table with SHA-256, confirmation dialog that shows the manifest and hashes before the download, the V-11 message when the plan is not validated, and the disclaimer text. Run commands from the `sketch2cad-web/` folder.

Implements: US-15, US-16, REQ-14, V-11

## Acceptance Criteria

- [ ] `pnpm test --run src/features/exports/ExportPage.test.tsx` passes: the download only fires after confirming the dialog that lists every hash.
- [ ] The final button is disabled with the V-11 message for a plan in `EDITING`.
