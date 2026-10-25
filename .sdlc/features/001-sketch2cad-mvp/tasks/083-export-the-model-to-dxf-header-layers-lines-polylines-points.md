---
id: 001-083
title: "Export the model to DXF: header, layers, lines, polylines, points"
status: todo
milestone: "H4 — Exportación y auditoría"
repository: sketch2cad
depends_on: [001-082, 001-027]
implements: [REQ-12]
files: ["sketch2cad-api/src/sketch2cad/export/__init__.py", "sketch2cad-api/src/sketch2cad/export/dxf.py", "sketch2cad-api/src/sketch2cad/export/dxf_layers.py", "sketch2cad-api/tests/export/test_dxf_core.py"]
parallelizable: false
estimate: { optimistic: 7, likely: 12, pessimistic: 20 }
labels: [ai-sdlc, python]
---

## Description

ezdxf R2013 (AC1027), `doc.units = M`, `$INSUNITS = 6`, `$MEASUREMENT = 1`. Layers and ACI colors only from the catalog; `line → LINE`, `polyline → LWPOLYLINE`, `point → POINT`. Metadata in XDATA (`plan_id`, `revision_number`, `snapshot_sha256`, `metric_status`, `generator_version`). Draft mode adds `DRAFT — NOT VALIDATED` in `L_SHEET`. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-12

## Acceptance Criteria

- [ ] Exporting a fixture snapshot and re-reading with ezdxf yields the same number of entities per layer and coordinates within 1e-6 m (CA-06, H4-1, `tests/export/test_dxf_core.py`).
- [ ] `doc.audit()` reports 0 errors.
- [ ] `doc.header['$INSUNITS'] == 6` and `doc.dxfversion == 'AC1027'`.
- [ ] No layer exists in the output that is not in the catalog: `{l.dxf.name for l in doc.layers} <= catalog_layers | {'0', 'Defpoints'}`.
