---
id: 001-029
title: "Implement the Storage interface and the filesystem CAS driver"
status: in_review
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-027]
implements: [REQ-02]
files: ["sketch2cad-api/src/sketch2cad/storage/__init__.py", "sketch2cad-api/src/sketch2cad/storage/base.py", "sketch2cad-api/src/sketch2cad/storage/filesystem.py", "sketch2cad-api/src/sketch2cad/storage/s3_stub.py", "sketch2cad-api/tests/storage/test_filesystem.py"]
parallelizable: true
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

`Storage.put(stream) -> (sha256, key)`, `get(key)`, `exists(key)`. The driver writes to a temp file, hashes while streaming, moves to `objects/sha256/ab/cd/<hash>` with mode 0444 and never overwrites an existing object (application-level immutability, see threat model in the Jot Down). The S3 driver is a stub raising `NotImplementedError`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-02

## Acceptance Criteria

- [ ] `put` of the same bytes twice returns the same key and leaves the file's mtime and content unchanged (`tests/storage/test_filesystem.py`).
- [ ] The stored file has mode `0o444` and `get(key)` returns bytes whose SHA-256 equals the returned hash.
- [ ] `put` of a stream that raises mid-way leaves no file under `objects/` (asserted by listing the directory).
