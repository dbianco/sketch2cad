---
id: 001-092
title: "Write the standalone sketch2cad-verify tool"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-091]
implements: [REQ-14, US-17, REQ-11]
files: ["sketch2cad-api/verifier/sketch2cad_verify.py", "sketch2cad-api/verifier/README.md", "sketch2cad-api/tests/package/test_verifier.py"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, python]
---

## Description

Single-file Python script depending only on `cryptography` and `rfc8785`. Recomputes artifact hashes, rebuilds the events chain from the package and checks the signature. Exit 0 on success; exit 1 naming the artifact or event that fails. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-14, US-17, REQ-11

## Acceptance Criteria

- [ ] The script verifies a freshly built package with exit 0 (`tests/package/test_verifier.py`).
- [ ] Altering one byte at each of 10 positions (manifest, DXF, PDF, audit, events) makes it exit 1 and print the affected name (CA-09, H4-5).
- [ ] `python verifier/sketch2cad_verify.py --help` runs in an environment containing only `cryptography` and `rfc8785`.
