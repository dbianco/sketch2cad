"""Recovery benchmark of ``suggest_lines`` on synthetic sketches (REQ-17, CA-04, H5-1).

Criterion: over at least 20 generated images, >= 90 % of the true segments longer than
20 mm of paper are recovered with vertex deviation < 2 mm, and there are 0 spurious
polylines shorter than 5 mm. All with the DEFAULT ``SuggestParams``.

Matching rules (shapely, px at 10 px/mm, tolerance 2 mm = 20 px):

* a polyline LIES ON a true segment when every vertex is within the tolerance of it
  (the segment's tolerance buffer is convex, so then the whole polyline is inside);
* a true segment is RECOVERED when some polyline lies on it and both polyline endpoints
  are within the tolerance of the segment endpoints (a fragment does not count);
* a polyline is SPURIOUS when it lies on no true segment.

Aggregated stats are printed (``pytest -s``) and written as JSON to ``$S2C_BENCH_OUT``
when that variable is set. Only the main set (non-crossing segments) is asserted; a set
with crossings is measured and reported, because a crossing has no unique answer.
"""

import json
import math
import os
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from shapely import LineString, Point

from sketch2cad.vision import PX_PER_MM, SuggestedPolyline, suggest_polylines
from sketch2cad.vision.synthetic_sketch import TrueSegment, generate_sketch

TOLERANCE_PX = 2.0 * PX_PER_MM
COUNTED_LONGER_THAN_MM = 20.0
SPURIOUS_SHORTER_THAN_MM = 5.0
MIN_RECALL = 0.90
SEEDS = range(24)
SEGMENTS_PER_IMAGE = 15
SIZE_PX = (2400, 1800)


@dataclass
class Stats:
    images: int = 0
    counted: int = 0
    recovered: int = 0
    deviations_px: list[float] = field(default_factory=lambda: [])
    endpoint_errors_px: list[float] = field(default_factory=lambda: [])
    spurious_lengths_mm: list[float] = field(default_factory=lambda: [])
    missed: list[dict[str, Any]] = field(default_factory=lambda: [])

    @property
    def recall(self) -> float:
        return self.recovered / self.counted if self.counted else 0.0

    @property
    def spurious_short(self) -> int:
        return sum(1 for length in self.spurious_lengths_mm if length < SPURIOUS_SHORTER_THAN_MM)

    def summary(self) -> dict[str, Any]:
        def mm(values: Sequence[float], fn: Any) -> float | None:
            return round(fn(values) / PX_PER_MM, 3) if values else None

        return {
            "images": self.images,
            "segments_longer_than_20mm": self.counted,
            "recovered": self.recovered,
            "recall": round(self.recall, 4),
            "mean_deviation_mm": mm(self.deviations_px, statistics.fmean),
            "max_deviation_mm": mm(self.deviations_px, max),
            "mean_endpoint_error_mm": mm(self.endpoint_errors_px, statistics.fmean),
            "max_endpoint_error_mm": mm(self.endpoint_errors_px, max),
            "spurious": len(self.spurious_lengths_mm),
            "spurious_shorter_than_5mm": self.spurious_short,
            "missed": self.missed[:20],
        }


def vertex_deviation(polyline: SuggestedPolyline, line: LineString) -> float:
    """Largest distance (px) from a polyline vertex to the true segment."""
    return max(line.distance(Point(point)) for point in polyline.points)


def endpoint_error(polyline: SuggestedPolyline, segment: TrueSegment) -> float:
    """Worst endpoint distance (px) under the better of the two endpoint pairings."""
    first, last = polyline.points[0], polyline.points[-1]
    a, b = segment.p0, segment.p1
    return min(
        max(math.dist(first, a), math.dist(last, b)), max(math.dist(first, b), math.dist(last, a))
    )


def score_image(
    stats: Stats, seed: int, segments: Sequence[TrueSegment], found: Sequence[SuggestedPolyline]
) -> None:
    lines = [segment.line() for segment in segments]
    stats.images += 1
    for polyline in found:
        if all(vertex_deviation(polyline, line) >= TOLERANCE_PX for line in lines):
            stats.spurious_lengths_mm.append(polyline.length_mm)
    for segment, line in zip(segments, lines, strict=True):
        if segment.length_mm <= COUNTED_LONGER_THAN_MM:
            continue
        stats.counted += 1
        matches = [
            (vertex_deviation(polyline, line), endpoint_error(polyline, segment))
            for polyline in found
        ]
        good = [m for m in matches if m[0] < TOLERANCE_PX and m[1] < TOLERANCE_PX]
        if good:
            deviation, error = min(good)
            stats.recovered += 1
            stats.deviations_px.append(deviation)
            stats.endpoint_errors_px.append(error)
        else:
            stats.missed.append(
                {"seed": seed, "p0": segment.p0, "p1": segment.p1, "mm": segment.length_mm}
            )


def run_benchmark(*, allow_crossings: bool) -> Stats:
    stats = Stats()
    for seed in SEEDS:
        sketch = generate_sketch(seed, SEGMENTS_PER_IMAGE, SIZE_PX, allow_crossings=allow_crossings)
        score_image(stats, seed, sketch.segments, suggest_polylines(sketch.image))
    return stats


def record(name: str, summary: dict[str, Any]) -> None:
    print(f"\n{name}: {json.dumps(summary, default=str)}")
    out = os.environ.get("S2C_BENCH_OUT")
    if not out:
        return
    path = Path(out)
    current: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    current[name] = summary
    path.write_text(json.dumps(current, indent=2, default=str))


def test_recovery_on_non_crossing_segments_meets_ca04() -> None:
    stats = run_benchmark(allow_crossings=False)
    record("non_crossing", stats.summary())

    assert stats.images >= 20
    assert stats.counted > 0
    assert stats.recall >= MIN_RECALL, stats.summary()
    assert stats.spurious_short == 0, stats.summary()


def test_recovery_with_crossings_is_measured_and_reported() -> None:
    """Crossings split strokes at junctions: reported for information, not asserted."""
    stats = run_benchmark(allow_crossings=True)
    record("with_crossings", stats.summary())

    assert stats.images >= 20
    assert stats.spurious_short == 0  # the pipeline never returns polylines under 5 mm


# Generator properties


def test_same_seed_gives_identical_sketch() -> None:
    a, b = generate_sketch(5, 10), generate_sketch(5, 10)
    assert np.array_equal(a.image, b.image)
    assert a.segments == b.segments
    assert a.meta == b.meta
    assert not np.array_equal(a.image, generate_sketch(6, 10).image)


def test_segments_follow_the_placement_rule() -> None:
    sketch = generate_sketch(3, 20, (3000, 2100))
    width, height = sketch.meta["size_px"]
    lines = [segment.line() for segment in sketch.segments]
    for i, line in enumerate(lines):
        minx, miny, maxx, maxy = line.bounds
        assert minx >= 50 and miny >= 50 and maxx <= width - 50 and maxy <= height - 50
        for other in lines[i + 1 :]:
            assert line.distance(other) >= 3.0 * PX_PER_MM
    for segment in sketch.segments:
        assert 5.0 <= segment.length_mm <= 150.0
        assert 3 <= segment.thickness_px <= 6
        assert segment.p0_mm == (segment.p0[0] / 10, segment.p0[1] / 10)


def test_truth_is_drawn_dark_on_light_paper() -> None:
    sketch = generate_sketch(8, 6, (1200, 900), blur_sigma_px=0)
    image = sketch.image
    assert image.dtype == np.uint8 and image.shape == (900, 1200)
    for segment in sketch.segments:
        mid = np.rint((np.array(segment.p0) + np.array(segment.p1)) / 2).astype(int)
        patch = image[mid[1] - 1 : mid[1] + 2, mid[0] - 1 : mid[0] + 2]
        assert int(patch.min()) < 150
    assert float(np.median(image)) > 200


def test_bgr_output_has_three_equal_channels() -> None:
    image = generate_sketch(1, 3, (600, 400), bgr=True).image
    assert image.shape == (400, 600, 3)
    assert np.array_equal(image[:, :, 0], image[:, :, 2])


def test_paper_specks_alone_give_no_suggestions() -> None:
    sketch = generate_sketch(2, 0, (2400, 1800), blur_sigma_px=0.8)
    assert int(sketch.image.min()) < 160  # specks are present
    mask = cv2.adaptiveThreshold(
        sketch.image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 10
    )
    assert np.count_nonzero(mask) > 0
    assert suggest_polylines(sketch.image) == []


def test_too_many_segments_for_the_sheet_is_an_error() -> None:
    with pytest.raises(ValueError, match="could not place"):
        generate_sketch(0, 200, (400, 300))
