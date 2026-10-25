---
id: 001-052
title: "Implement entity create, read, patch and delete with provenance"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-049]
implements: [REQ-05, US-06, REQ-10]
files: ["sketch2cad-api/src/sketch2cad/entities/__init__.py", "sketch2cad-api/src/sketch2cad/entities/schemas.py", "sketch2cad-api/src/sketch2cad/entities/service.py", "sketch2cad-api/src/sketch2cad/api/routers/entities.py", "sketch2cad-api/tests/entities/test_entities_api.py"]
parallelizable: false
estimate: { optimistic: 8, likely: 14, pessimistic: 24 }
labels: [ai-sdlc, python]
---

## Description

Pydantic models per Spec §8.2 (types line, polyline, point, text, dimension, symbol). `GET/POST /revisions/{id}/entities`, `PATCH/DELETE /entities/{id}` with `operation` in {`move_vertex`, `snap_orthogonal`, `snap_endpoint`, `snap_grid`, `relabel`, `reclassify`} and optional `reason`. Every change writes `entity.created|updated|deleted` with before and after. Soft delete via `deleted_at`. `source` is preserved on edit (history stays in events). Run commands from the `sketch2cad-api/` folder.

Implements: REQ-05, US-06, REQ-10

## Acceptance Criteria

- [ ] Creating a polyline with 3 vertices and moving vertex 1 yields an `entity.updated` event whose `before` and `after` differ only in that vertex (`tests/entities/test_entities_api.py`).
- [ ] Editing keeps `source.method` unchanged.
- [ ] `DELETE` sets `deleted_at` and the entity disappears from `GET /revisions/{id}/entities` but the row remains.
- [ ] Every snapping operation writes an event with the operation name (US-06 criterion).
