---
id: 001-037
title: "Implement the image quality checks"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-001]
implements: [REQ-02]
files: ["sketch2cad-api/src/sketch2cad/qc/__init__.py", "sketch2cad-api/src/sketch2cad/qc/checks.py", "sketch2cad-api/src/sketch2cad/qc/synthetic.py", "sketch2cad-api/tests/qc/test_checks.py"]
parallelizable: true
estimate: { optimistic: 5, likely: 8, pessimistic: 14 }
labels: [ai-sdlc, python]
---

## Description

Pure functions on a numpy image: short side, Laplacian variance, contrast p95−p5 and paper-edge cropping. Thresholds from configuration (defaults: short side amber < 1500 and red < 900; Laplacian variance amber < 100 and red < 40; contrast amber < 60). Result `{status, checks[]}`. `synthetic.py` generates test images with OpenCV. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-02

## Acceptance Criteria

- [ ] A 800 px short-side image is red, a 1200 px is amber, a sharp 3000 px is green; an image with Laplacian variance 30 is red (H1-4, `tests/qc/test_checks.py`).
- [ ] Warnings never raise: `evaluate` always returns a result object.
- [ ] Thresholds are read from config: overriding `qc.min_short_side_red` to 1000 turns the 800 px case red and a 950 px case amber.
