---
id: 001-026
title: "Run spike 2: DXF byte-level reproducibility with ezdxf"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-001]
implements: [REQ-12, REQ-16]
files: ["sketch2cad-api/spikes/dxf_repro.py", "sketch2cad-api/spikes/fixtures/plan_fixture.json", "docs/spikes/dxf-reproducibility.md", "sketch2cad-api/tests/test_spike_dxf_repro.py"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

Generate an R2013 DXF from a fixture (one polyline, one text, one block with attribute, one dimension) in two separate processes started 2 seconds apart; compare bytes; list every differing header variable (`$TDCREATE`, `$TDUPDATE`, `$FINGERPRINTGUID`, `$VERSIONGUID`) and handle; apply the fixes that make the output stable and document them. Resolves decision D-3. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-12, REQ-16

## Acceptance Criteria

- [ ] `uv run python spikes/dxf_repro.py` prints the SHA-256 of both runs and the list of differing fields (empty after fixes).
- [ ] `uv run pytest tests/test_spike_dxf_repro.py -q` passes: after applying the documented settings, two subprocess runs 2 s apart produce the same SHA-256, or the test documents which fields remain different.
- [ ] `docs/spikes/dxf-reproducibility.md` ends with a one-line recommendation: byte-identical or canonical-content.
