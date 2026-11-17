"""Generate e2e/fixtures/dimensioned_sketch.png and its layout dimensioned_sketch.json.

The fixture of e2e/suggestions_ocr.spec.ts (H5, US-12, US-13): a sharp synthetic sketch
photo (the sheet of paper on a dark desk, like ``sketch2cad.qc.synthetic.sketch``, so every
quality check is green) with clean straight walls for ``suggest_lines`` and one written
dimension, ``3,50``, above the top wall for the OCR crop tool. The spec reads the layout
from the JSON (pixel boxes of the dimension text and of a blank patch of paper), so the
PNG and the numbers it checks against always come from this one script.

Deterministic: OpenCV Hershey fonts and lines only, no system fonts and no randomness.
Run from sketch2cad-web/ (OpenCV and NumPy come from the API environment):

    uv run --project ../sketch2cad-api python e2e/fixtures/make_dimensioned_sketch.py

Font choice, checked with the worker's TrOCR model (trocr-base-handwritten at the pinned
revision, CPU, macOS arm64) on the box of the layout and on boxes shifted by 12 px:

* HERSHEY_PLAIN 2.5 (any thickness 5-6): '3,50', confidence 0.79 (used);
* HERSHEY_PLAIN 3.0-4.0: '3.50', confidence 0.83-0.86 (also 3.5 m);
* HERSHEY_SIMPLEX, SCRIPT_SIMPLEX, DUPLEX and COMPLEX at 3.0-4.0 mostly read the comma as
  a digit ('3.350', '3,350': ambiguous, no value), so those fonts were not used;
* the blank box reads '0 0' (no value, confidence 0.52).
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

from sketch2cad.qc.checks import evaluate
from sketch2cad.qc.synthetic import DESK_BGR, INK_BGR, PAPER_BGR

HERE = Path(__file__).resolve().parent
PNG = HERE / "dimensioned_sketch.png"
LAYOUT = HERE / "dimensioned_sketch.json"

WIDTH, HEIGHT = 2000, 2828
"""Portrait A-series ratio; the short side is above qc.min_short_side_amber (1500 px)."""
MARGIN = 0.06
WALL_PX = 6
DIMENSION_TEXT = "3,50"
FONT = cv2.FONT_HERSHEY_PLAIN
FONT_SCALE = 2.5
FONT_THICKNESS = 6
# Room walls (x0, y0, x1, y1) and one interior wall, in image px.
ROOM = (400, 900, 1600, 2000)
INTERIOR = ((1000, 2000), (1000, 1500))
DIMENSION_GAP_PX = 60
"""Space between the bottom of the dimension text and the top wall."""
BOX_PAD_PX = 30
"""Margin of the OCR box around the text, as a user would draw it."""
BLANK_BOX = (300, 2250, 520, 2350)
"""A patch of blank paper (no ink) for the low-confidence OCR case."""


def gray(bgr: tuple[int, int, int]) -> int:
    return round(sum(bgr) / 3)


def draw() -> tuple[np.ndarray, dict[str, object]]:
    image = np.full((HEIGHT, WIDTH), gray(DESK_BGR), dtype=np.uint8)
    ink = gray(INK_BGR)
    pad_x, pad_y = round(WIDTH * MARGIN), round(HEIGHT * MARGIN)
    cv2.rectangle(image, (pad_x, pad_y), (WIDTH - pad_x, HEIGHT - pad_y), gray(PAPER_BGR), -1)
    x0, y0, x1, y1 = ROOM
    cv2.rectangle(image, (x0, y0), (x1, y1), ink, WALL_PX, cv2.LINE_AA)
    cv2.line(image, INTERIOR[0], INTERIOR[1], ink, WALL_PX, cv2.LINE_AA)
    (text_w, text_h), baseline = cv2.getTextSize(DIMENSION_TEXT, FONT, FONT_SCALE, FONT_THICKNESS)
    origin = ((x0 + x1 - text_w) // 2, y0 - DIMENSION_GAP_PX - baseline)
    cv2.putText(
        image, DIMENSION_TEXT, origin, FONT, FONT_SCALE, ink, FONT_THICKNESS, cv2.LINE_AA
    )
    text_box = [
        origin[0] - BOX_PAD_PX,
        origin[1] - text_h - BOX_PAD_PX,
        origin[0] + text_w + BOX_PAD_PX,
        origin[1] + baseline + BOX_PAD_PX,
    ]
    layout: dict[str, object] = {
        "generator": "e2e/fixtures/make_dimensioned_sketch.py",
        "size_px": [WIDTH, HEIGHT],
        "dimension": {"text": DIMENSION_TEXT, "value_m": 3.5, "box_px": text_box},
        "top_wall_px": [[x0, y0], [x1, y0]],
        "blank_box_px": list(BLANK_BOX),
    }
    return image, layout


def main() -> int:
    image, layout = draw()
    qc = evaluate(image).to_dict()
    if qc["status"] != "green":
        print(f"QC is not green: {json.dumps(qc)}", file=sys.stderr)
        return 1
    layout["qc"] = {check["name"]: check["value"] for check in qc["checks"]}
    cv2.imwrite(str(PNG), image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    LAYOUT.write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {PNG.name} and {LAYOUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
