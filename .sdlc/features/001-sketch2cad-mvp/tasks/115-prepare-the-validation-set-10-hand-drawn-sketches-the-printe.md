---
id: 001-115
title: "Prepare the validation set: 10 hand-drawn sketches, the printed pattern and the synthetic set"
status: partial
unmet: ["validation/sketches holds fewer than 10 hand-drawn sketches (needs the owner's drawings)", "validation/pattern has no oblique photo of the printed pattern", "README tables of declared dimensions are empty templates"]
milestone: "H6 — Endurecimiento y validación interna"
repository: sketch2cad
depends_on: [001-098]
implements: [REQ-20]
files: ["validation/README.md", "validation/sketches/.gitkeep", "validation/pattern/.gitkeep", "validation/synthetic/generate.py", "validation/synthetic/.gitkeep"]
parallelizable: true
estimate: { optimistic: 8, likely: 14, pessimistic: 24 }
labels: [ai-sdlc, docs]
---

## Description

Draw at least 10 sketches by hand following field practice (schematic sketches with dimensions, triangulations from two corners, baseline plus offset on a road), photograph the printed calibration pattern, and generate the synthetic set. No real case data. Record for each sketch the declared dimensions in `validation/README.md`.

Implements: REQ-20

## Acceptance Criteria

- [ ] `ls validation/sketches | wc -l` prints at least 10 and the README lists the declared dimensions of each (H6-5).
- [ ] `validation/pattern/` holds at least one oblique photo of the pattern with its known dimensions in the README.
- [ ] `python validation/synthetic/generate.py --count 20` creates 20 images with ground truth JSON.
