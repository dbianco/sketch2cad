---
id: 001-045
title: "Document and script backup and restore"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-034]
implements: [INFRA]
files: ["docs/backup.md", "sketch2cad-api/scripts/backup.sh", "sketch2cad-api/scripts/restore_check.sh", "sketch2cad-api/tests/test_backup_scripts.py"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 8 }
labels: [ai-sdlc, python]
---

## Description

`backup.sh` runs `pg_dump` plus a tar of the objects directory into a dated folder. `restore_check.sh` restores both into a scratch database and directory and compares SHA-256 of every original and every frozen snapshot with the source. Run commands from the `sketch2cad-api/` folder.

Implements: INFRA

## Acceptance Criteria

- [ ] `bash scripts/backup.sh` creates `pg.dump` and `objects.tar` in the target folder.
- [ ] `bash scripts/restore_check.sh <backup>` exits 0 on an unmodified backup and exits 1, naming the object, when one byte of an object in the backup is changed (`tests/test_backup_scripts.py`).
