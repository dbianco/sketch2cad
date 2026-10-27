"""Deterministic synthetic rectified sketches with known straight segments (REQ-17, CA-04).

Used by the ``suggest_lines`` recovery benchmark and performance test. Every image comes
from a ``numpy.random.Generator`` seeded explicitly, so a seed always gives the same
pixels and the same truth. The image is a rectified sheet at ``px_per_mm`` px per paper
mm; truth coordinates are ``(x, y)`` = (column, row) px like the pipeline output.

Strokes:

* lengths uniform in [``MIN_LENGTH_MM``, ``MAX_LENGTH_MM``] paper mm, orientation uniform;
* thickness an integer uniform in [3, 6] px, drawn anti-aliased (``cv2.LINE_AA``);
* the stroke is a polyline sampled every ``JITTER_STEP_PX`` px with a Gaussian
  perpendicular jitter of ``JITTER_SIGMA_PX`` px (endpoints included);
* ink gray level uniform in [``INK_DARKEST``, ``INK_LIGHTEST``] (pencil to marker).

Placement rule (rejection sampling): a segment lies entirely inside the sheet with a
``BORDER_MM`` margin and, unless ``allow_crossings``, its distance to every segment already
placed is at least ``min_separation_mm`` (default 3 mm of paper). That one rule excludes
crossings, overlaps, T-junctions and near-collinear touching, so every true segment is
an isolated stroke and recovery is well defined. With ``allow_crossings`` segments are
placed anywhere (junctions appear); that set has no unique expected answer.

Paper: base gray ~240 with Gaussian noise, a low-frequency illumination field (linear
gradient plus a smooth random field), dark specks of 1 or 2 px (smaller than the 3 x 3
opening of the pipeline, so they must be removed) and an optional slight Gaussian blur.
"""

import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
from shapely import LineString

from sketch2cad.calibration.rectify import PX_PER_MM

MIN_LENGTH_MM = 5.0
MAX_LENGTH_MM = 150.0
MIN_THICKNESS_PX = 3
MAX_THICKNESS_PX = 6
JITTER_SIGMA_PX = 0.3
JITTER_STEP_PX = 6.0
INK_DARKEST = 30
INK_LIGHTEST = 110
PAPER_GRAY = 240.0
PAPER_NOISE_SIGMA = 4.0
ILLUMINATION_AMPLITUDE = 18.0
"""Peak-to-peak gray change of the illumination field across the sheet."""
SPECKS_PER_MEGAPIXEL = 60
BORDER_MM = 5.0
DEFAULT_MIN_SEPARATION_MM = 3.0
MAX_ATTEMPTS_PER_SEGMENT = 2000

_SHIFT = 4
"""Fixed-point bits for sub-pixel stroke coordinates in ``cv2.polylines``."""

Image = npt.NDArray[np.uint8]
PointXY = tuple[float, float]


@dataclass(frozen=True)
class TrueSegment:
    """A drawn straight segment: ideal endpoints in px (before jitter) and its stroke."""

    p0: PointXY
    p1: PointXY
    thickness_px: int
    ink: int
    px_per_mm: float = PX_PER_MM

    @property
    def length_px(self) -> float:
        return math.dist(self.p0, self.p1)

    @property
    def length_mm(self) -> float:
        return self.length_px / self.px_per_mm

    @property
    def p0_mm(self) -> PointXY:
        return (self.p0[0] / self.px_per_mm, self.p0[1] / self.px_per_mm)

    @property
    def p1_mm(self) -> PointXY:
        return (self.p1[0] / self.px_per_mm, self.p1[1] / self.px_per_mm)

    def line(self) -> LineString:
        """The segment as a shapely ``LineString`` in px."""
        return LineString([self.p0, self.p1])


@dataclass(frozen=True)
class SyntheticSketch:
    """Image (gray, or BGR when requested) plus the truth segments and generation metadata."""

    image: Image
    segments: tuple[TrueSegment, ...]
    meta: dict[str, Any] = field(default_factory=lambda: {})


def generate_sketch(
    seed: int,
    n_segments: int,
    size_px: tuple[int, int] = (2400, 1800),
    px_per_mm: float = PX_PER_MM,
    *,
    allow_crossings: bool = False,
    min_separation_mm: float = DEFAULT_MIN_SEPARATION_MM,
    blur_sigma_px: float | None = None,
    bgr: bool = False,
) -> SyntheticSketch:
    """A synthetic sketch of ``size_px`` = (width, height) with ``n_segments`` strokes.

    ``blur_sigma_px`` None draws a slight blur in [0, 0.8] px from the generator; 0 disables
    it. Raises ``ValueError`` when the segments cannot be placed under the placement rule
    (too many segments for the sheet).
    """
    width, height = size_px
    if n_segments < 0 or width <= 0 or height <= 0:
        raise ValueError("n_segments must be >= 0 and the size positive")
    rng = np.random.default_rng(seed)
    segments = _place_segments(
        rng, n_segments, width, height, px_per_mm, allow_crossings, min_separation_mm
    )
    canvas = _paper(rng, width, height)
    for segment in segments:
        _draw_stroke(rng, canvas, segment)
    image = np.clip(canvas, 0, 255)
    _add_specks(rng, image)
    blur = float(rng.uniform(0.0, 0.8)) if blur_sigma_px is None else blur_sigma_px
    if blur > 0:
        image = cv2.GaussianBlur(image, (0, 0), sigmaX=blur)
    gray = np.clip(np.rint(image), 0, 255).astype(np.uint8)
    meta: dict[str, Any] = {
        "seed": seed,
        "size_px": (width, height),
        "px_per_mm": px_per_mm,
        "allow_crossings": allow_crossings,
        "min_separation_mm": None if allow_crossings else min_separation_mm,
        "blur_sigma_px": blur,
        "jitter_sigma_px": JITTER_SIGMA_PX,
    }
    out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if bgr else gray
    return SyntheticSketch(np.asarray(out, dtype=np.uint8), tuple(segments), meta)


def _place_segments(
    rng: np.random.Generator,
    n_segments: int,
    width: int,
    height: int,
    px_per_mm: float,
    allow_crossings: bool,
    min_separation_mm: float,
) -> list[TrueSegment]:
    border = BORDER_MM * px_per_mm
    separation = min_separation_mm * px_per_mm
    placed: list[TrueSegment] = []
    lines: list[LineString] = []
    for _ in range(n_segments):
        for _attempt in range(MAX_ATTEMPTS_PER_SEGMENT):
            length = float(rng.uniform(MIN_LENGTH_MM, MAX_LENGTH_MM)) * px_per_mm
            angle = float(rng.uniform(0.0, math.pi))
            x0 = float(rng.uniform(border, width - border))
            y0 = float(rng.uniform(border, height - border))
            x1, y1 = x0 + length * math.cos(angle), y0 + length * math.sin(angle)
            if not (border <= x1 <= width - border and border <= y1 <= height - border):
                continue
            candidate = LineString([(x0, y0), (x1, y1)])
            if not allow_crossings and any(candidate.distance(o) < separation for o in lines):
                continue
            thickness = int(rng.integers(MIN_THICKNESS_PX, MAX_THICKNESS_PX + 1))
            ink = int(rng.integers(INK_DARKEST, INK_LIGHTEST + 1))
            placed.append(TrueSegment((x0, y0), (x1, y1), thickness, ink, px_per_mm))
            lines.append(candidate)
            break
        else:
            raise ValueError(f"could not place segment {len(placed) + 1} of {n_segments}")
    return placed


def _paper(rng: np.random.Generator, width: int, height: int) -> npt.NDArray[np.float32]:
    """Paper gray levels: base + linear gradient + smooth random field + Gaussian noise."""
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    angle = float(rng.uniform(0.0, 2 * math.pi))
    ramp = xs / width * math.cos(angle) + ys / height * math.sin(angle)
    coarse = rng.normal(0.0, 1.0, (4, 5)).astype(np.float32)
    field_: npt.NDArray[np.float32] = np.asarray(
        cv2.resize(coarse, (width, height), interpolation=cv2.INTER_CUBIC), dtype=np.float32
    )
    span = (float(field_.max()) - float(field_.min())) or 1.0
    light = 0.5 * ramp + 0.5 * (field_ - float(field_.min())) / span
    light = (light - float(light.mean())) * ILLUMINATION_AMPLITUDE
    noise = rng.normal(0.0, PAPER_NOISE_SIGMA, (height, width)).astype(np.float32)
    return (PAPER_GRAY + light + noise).astype(np.float32)


def _draw_stroke(
    rng: np.random.Generator, canvas: npt.NDArray[np.float32], segment: TrueSegment
) -> None:
    """Anti-aliased jittered stroke, multiplied into the paper so its texture shows through."""
    (x0, y0), (x1, y1) = segment.p0, segment.p1
    steps = max(1, math.ceil(segment.length_px / JITTER_STEP_PX))
    t = np.linspace(0.0, 1.0, steps + 1)
    nx, ny = -(y1 - y0) / segment.length_px, (x1 - x0) / segment.length_px
    offset = rng.normal(0.0, JITTER_SIGMA_PX, steps + 1)
    xs = x0 + t * (x1 - x0) + offset * nx
    ys = y0 + t * (y1 - y0) + offset * ny
    points = np.rint(np.stack([xs, ys], axis=1) * (1 << _SHIFT)).astype(np.int32)
    pad = segment.thickness_px + 2
    left = max(0, int(min(xs.min(), xs.max())) - pad)
    top = max(0, int(min(ys.min(), ys.max())) - pad)
    right = min(canvas.shape[1], int(xs.max()) + pad + 1)
    bottom = min(canvas.shape[0], int(ys.max()) + pad + 1)
    coverage = np.zeros((bottom - top, right - left), dtype=np.uint8)
    local = points - np.array([left << _SHIFT, top << _SHIFT], dtype=np.int32)
    cv2.polylines(coverage, [local], False, 255, segment.thickness_px, cv2.LINE_AA, shift=_SHIFT)
    alpha = coverage.astype(np.float32) / 255.0
    window = canvas[top:bottom, left:right]
    darkness = segment.ink / PAPER_GRAY
    window *= 1.0 - alpha * (1.0 - darkness)


def _add_specks(rng: np.random.Generator, image: npt.NDArray[np.float32]) -> None:
    """Dark specks of 1 x 1, 1 x 2, 2 x 1 or 2 x 2 px (all smaller than a 3 x 3 opening)."""
    height, width = image.shape
    count = round(SPECKS_PER_MEGAPIXEL * width * height / 1_000_000)
    rows = rng.integers(0, height - 1, count)
    cols = rng.integers(0, width - 1, count)
    tall = rng.integers(1, 3, count)
    wide = rng.integers(1, 3, count)
    gray = rng.uniform(INK_DARKEST, INK_LIGHTEST, count)
    for row, col, h, w, g in zip(rows, cols, tall, wide, gray, strict=True):
        image[row : row + h, col : col + w] = g
