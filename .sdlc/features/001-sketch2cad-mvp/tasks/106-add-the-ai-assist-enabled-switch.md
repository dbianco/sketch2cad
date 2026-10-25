---
id: 001-106
title: "Add the ai_assist.enabled switch"
status: todo
milestone: "H5 — Asistencia de IA"
repository: sketch2cad
depends_on: [001-105, 001-103]
implements: [REQ-17, REQ-18, REQ-19]
files: ["sketch2cad-api/src/sketch2cad/api/routers/jobs.py", "sketch2cad-api/src/sketch2cad/config_params.py", "sketch2cad-api/tests/jobs/test_ai_switch.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 3, pessimistic: 5 }
labels: [ai-sdlc, python]
---

## Description

With `ai_assist.enabled = false` the `suggest_lines` and `ocr_crop` job kinds return 404 and the plan config endpoint reports the switch so the UI hides controls. Not a Split flag. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-17, REQ-18, REQ-19

## Acceptance Criteria

- [ ] With the switch off, `POST /plans/{id}/jobs {kind: 'suggest_lines'}` returns 404 and calibration acceptance enqueues nothing (H5-7, `tests/jobs/test_ai_switch.py`).
- [ ] Changing the switch writes a `config.changed` event.
