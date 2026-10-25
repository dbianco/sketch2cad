---
id: 001-055
title: "Implement revision freezing and reopening with canonical snapshots"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-052, 001-030]
implements: [REQ-10, US-14]
files: ["sketch2cad-api/src/sketch2cad/revisions/__init__.py", "sketch2cad-api/src/sketch2cad/revisions/snapshot.py", "sketch2cad-api/src/sketch2cad/revisions/service.py", "sketch2cad-api/tests/revisions/test_revisions.py"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, python]
---

## Description

`freeze(revision)` builds the snapshot of Spec §8.3 (entities sorted by id, deleted entities excluded), serializes with RFC 8785, stores `snapshot_sha256`, sets `state = frozen`, writes `revision.frozen`. `reopen(plan)` creates revision n+1 as `draft` with `parent_revision_id` and copies entities; the previous revision stays untouched. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-10, US-14

## Acceptance Criteria

- [ ] Freezing then reading the stored snapshot returns bytes identical to the first serialization (H2-8, `tests/revisions/test_revisions.py`).
- [ ] Reopening creates revision n+1 with `parent_revision_id == n.id`, and revision n keeps its `snapshot_sha256`.
- [ ] Serializing the same entities in a different insertion order yields the same `snapshot_sha256`.
