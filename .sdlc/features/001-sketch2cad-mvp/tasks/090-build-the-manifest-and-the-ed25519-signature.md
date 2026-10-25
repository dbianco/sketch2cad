---
id: 001-090
title: "Build the manifest and the Ed25519 signature"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-055, 001-031]
implements: [REQ-14]
files: ["sketch2cad-api/src/sketch2cad/package/__init__.py", "sketch2cad-api/src/sketch2cad/package/manifest.py", "sketch2cad-api/src/sketch2cad/package/signing.py", "sketch2cad-api/src/sketch2cad/cli.py", "sketch2cad-api/tests/package/test_manifest_signing.py"]
parallelizable: true
estimate: { optimistic: 8, likely: 14, pessimistic: 24 }
labels: [ai-sdlc, python]
---

## Description

Manifest per Spec §14.2 serialized with RFC 8785; signature over the manifest without the `signature` field with `cryptography` Ed25519, scheme `ed25519-jcs-v1`. CLI command `generate-signing-key` writes a key file with mode 0600. The app refuses to start if `SIGNING_KEY_PATH` is missing or its mode is above 0600. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-14

## Acceptance Criteria

- [ ] Signing and verifying the sample manifest passes; changing one byte of the manifest makes verification fail (`tests/package/test_manifest_signing.py`).
- [ ] Startup with a key file of mode 0644 exits non-zero with a message naming the path (H4-11).
- [ ] `rg -n 'BEGIN (PRIVATE|OPENSSH)' .` finds no key material in the repository (gitleaks job also green).
