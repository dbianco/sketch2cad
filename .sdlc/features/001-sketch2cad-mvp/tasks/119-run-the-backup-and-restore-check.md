---
id: 001-119
title: "Run the backup and restore check"
status: todo
kind: acceptance-run
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-113]
implements: [REQ-10, REQ-11]
files: ["docs/restore-report.md"]
parallelizable: false
estimate: { optimistic: 2, likely: 3, pessimistic: 5 }
labels: [ai-sdlc, python]
---

## Description

Run `scripts/backup.sh` and `scripts/restore_check.sh` on a populated instance and record the result. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-10, REQ-11

## Acceptance Criteria

- [ ] `bash scripts/restore_check.sh <backup>` exits 0 and the report lists the number of originals and snapshots whose SHA-256 matched (H6-7).
