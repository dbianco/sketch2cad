---
id: 001-099
title: "Implement layered configuration with per-incident-type overrides"
status: todo
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-098, 001-095]
implements: [REQ-19]
files: ["sketch2cad-api/src/sketch2cad/config_params.py", "sketch2cad-api/src/sketch2cad/db/models/config_override.py", "sketch2cad-api/migrations/versions/0006_config_override.py", "sketch2cad-api/tests/test_config_params.py"]
parallelizable: false
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

Parameters (QC thresholds, tolerances of Spec §11.4, `suggest_lines` parameters, OCR threshold, `ai_assist.enabled`) resolve in order: incident-type override, global override, code default. Admin changes write `config.changed` events. Existing hardcoded defaults move to this module. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-19

## Acceptance Criteria

- [ ] `get_param('dimension.discrepancy_tolerance_m', incident_type='traffic_collision')` returns the override when set and the global default otherwise (`tests/test_config_params.py`).
- [ ] `rg -n '0\.02|1\.0\b' src/sketch2cad/geometry src/sketch2cad/calibration | rg -v 'config_params|test'` returns no hardcoded tolerance literals (citation for the repo-wide claim).
- [ ] Setting an override writes one `config.changed` event.
