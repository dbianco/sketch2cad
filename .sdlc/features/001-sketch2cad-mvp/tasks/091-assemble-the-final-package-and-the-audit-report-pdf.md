---
id: 001-091
title: "Assemble the final package and the audit report PDF"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-090, 001-088, 001-089]
implements: [REQ-14, US-16]
files: ["sketch2cad-api/src/sketch2cad/package/build.py", "sketch2cad-api/src/sketch2cad/package/audit_report.py", "sketch2cad-api/src/sketch2cad/package/verify_md.py", "sketch2cad-api/tests/package/test_build.py"]
parallelizable: false
estimate: { optimistic: 8, likely: 14, pessimistic: 24 }
labels: [ai-sdlc, python]
---

## Description

`build_package` produces `manifest.json`, `plan.dxf`, `plan.pdf`, `audit.pdf` (readable manifest, plan events, validity note, disclaimer, verification instructions), the original source, `VERIFY.md` and the public key, zipped. States that the signature proves integrity and technical origin, not expert authorship. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-14, US-16

## Acceptance Criteria

- [ ] The ZIP contains exactly the six artifact kinds plus `VERIFY.md` and the public key (`tests/package/test_build.py`).
- [ ] The audit PDF text contains "integridad y origen técnico" and "no autoría pericial".
- [ ] Every `sha256` in `manifest.json` equals the SHA-256 of the corresponding file in the ZIP.
