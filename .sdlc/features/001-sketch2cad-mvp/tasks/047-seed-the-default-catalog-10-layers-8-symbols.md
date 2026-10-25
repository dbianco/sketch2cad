---
id: 001-047
title: "Seed the default catalog (10 layers, 8 symbols)"
status: todo
milestone: "H2 — Calibración y editor"
repository: sketch2cad
depends_on: [001-046]
implements: [REQ-09]
files: ["sketch2cad-api/src/sketch2cad/catalog/__init__.py", "sketch2cad-api/src/sketch2cad/catalog/default_1.py", "sketch2cad-api/src/sketch2cad/catalog/symbols.py", "sketch2cad-api/migrations/versions/0004_seed_catalog.py", "sketch2cad-api/src/sketch2cad/api/routers/catalog.py", "sketch2cad-api/tests/catalog/test_default_catalog.py"]
parallelizable: false
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

Layers `L_STRUCTURE`, `L_ACCESS`, `L_EVIDENCE`, `L_SYMBOLS`, `L_DIMENSIONS`, `L_TEXT`, `L_ORIENTATION`, `L_ANNOTATIONS`, `L_CONTROL`, `L_SHEET` with ACI colors from Spec §12.1. Symbols `north_arrow`, `evidence_marker`, `control_point`, `door`, `window`, `generic_vehicle`, `traffic_light`, `tree`, each with SVG, DXF block definition, `size_mode`, default size and attributes. `GET /api/v1/catalog/active` returns the active catalog. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-09

## Acceptance Criteria

- [ ] The seeded catalog has 10 layers and 8 symbols (H2-13, `tests/catalog/test_default_catalog.py`).
- [ ] No `symbol_id` contains `victim`, `blood`, `weapon` or `casing`: `rg -in 'victim|blood|weapon|casing|vict|arma|sangre|casquillo' src/sketch2cad/catalog/` returns no matches.
- [ ] Each symbol has a non-empty `svg` and a `dxf_block.entities` list, and `size_mode` is `paper_mm` or `world_m`.
