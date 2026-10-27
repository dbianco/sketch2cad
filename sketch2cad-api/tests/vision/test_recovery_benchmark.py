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

Fragmentation (a wall must come out whole so one dimension can be declared on it):

* POLYLINES PER WALL = polylines lying on a counted true segment / counted segments; at
  most :data:`MAX_POLYLINES_PER_WALL` on the non-crossing set, with and without 1-3 px
  stroke gaps;
* on the T-junction sets (``generate_t_junctions``, bars crossed by stems) polylines may
  turn at a corner, so SEGMENTS of the output are matched instead: SEGMENTS PER WALL
  (segments lying on the wall / walls) and LONGEST-SEGMENT RECOVERY (longest segment lying
  on the wall / wall length, averaged; capped at 1). At HEAD 8ad7509, before the joining
  stage, the measured values were: gaps set 2.0 polylines per wall and longest-segment
  recovery 0.59; T set 1.31 segments per wall and 0.89; T with gaps 2.24 and 0.55.

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
from sketch2cad.vision.synthetic_sketch import (
    SyntheticSketch,
    TrueSegment,
    generate_sketch,
    generate_t_junctions,
)

TOLERANCE_PX = 2.0 * PX_PER_MM
COUNTED_LONGER_THAN_MM = 20.0
SPURIOUS_SHORTER_THAN_MM = 5.0
MIN_RECALL = 0.90
MAX_POLYLINES_PER_WALL = 1.2
MAX_SEGMENTS_PER_WALL = 1.2
MIN_LONGEST_SEGMENT_RECOVERY = 0.9
STROKE_GAPS_PX = (1, 3)
T_SEEDS = range(12)
T_BARS_PER_IMAGE = 6
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
    on_wall: int = 0

    @property
    def recall(self) -> float:
        return self.recovered / self.counted if self.counted else 0.0

    @property
    def polylines_per_wall(self) -> float:
        return self.on_wall / self.counted if self.counted else 0.0

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
            "polylines_per_wall": round(self.polylines_per_wall, 4),
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
        stats.on_wall += sum(1 for deviation, _ in matches if deviation < TOLERANCE_PX)
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


def run_benchmark(*, allow_crossings: bool, gap_px: tuple[int, int] | None = None) -> Stats:
    stats = Stats()
    for seed in SEEDS:
        sketch = generate_sketch(
            seed, SEGMENTS_PER_IMAGE, SIZE_PX, allow_crossings=allow_crossings, gap_px=gap_px
        )
        score_image(stats, seed, sketch.segments, suggest_polylines(sketch.image))
    return stats


@dataclass
class SegmentStats:
    walls: int = 0
    segments_on_walls: int = 0
    longest_recovery: list[float] = field(default_factory=lambda: [])
    whole: int = 0
    shortest_polyline_mm: float = math.inf

    def summary(self) -> dict[str, Any]:
        return {
            "walls_longer_than_20mm": self.walls,
            "segments_per_wall": round(self.segments_on_walls / self.walls, 4),
            "mean_longest_segment_recovery": round(statistics.fmean(self.longest_recovery), 4),
            "min_longest_segment_recovery": round(min(self.longest_recovery), 4),
            "whole_walls": self.whole,
            "shortest_polyline_mm": round(self.shortest_polyline_mm, 3),
        }


def score_segments(stats: SegmentStats, sketch: SyntheticSketch) -> None:
    """Match the output SEGMENTS (consecutive vertices) to the true walls."""
    found = suggest_polylines(sketch.image)
    pieces = [
        (a, b)
        for polyline in found
        for a, b in zip(polyline.points, polyline.points[1:], strict=False)
    ]
    for polyline in found:
        stats.shortest_polyline_mm = min(stats.shortest_polyline_mm, polyline.length_mm)
    for segment in sketch.segments:
        if segment.length_mm <= COUNTED_LONGER_THAN_MM:
            continue
        line = segment.line()
        on = [
            (a, b)
            for a, b in pieces
            if line.distance(Point(a)) < TOLERANCE_PX and line.distance(Point(b)) < TOLERANCE_PX
        ]
        stats.walls += 1
        stats.segments_on_walls += len(on)
        longest = max((math.dist(a, b) for a, b in on), default=0.0)
        stats.longest_recovery.append(min(1.0, longest / segment.length_px))
        ends = (segment.p0, segment.p1)
        stats.whole += any(
            min(
                max(math.dist(a, ends[0]), math.dist(b, ends[1])),
                max(math.dist(a, ends[1]), math.dist(b, ends[0])),
            )
            < TOLERANCE_PX
            for a, b in on
        )


def run_t_junctions(gap_px: tuple[int, int] | None) -> SegmentStats:
    stats = SegmentStats()
    for seed in T_SEEDS:
        score_segments(stats, generate_t_junctions(seed, T_BARS_PER_IMAGE, SIZE_PX, gap_px=gap_px))
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
    assert stats.polylines_per_wall <= MAX_POLYLINES_PER_WALL, stats.summary()


def test_strokes_with_small_gaps_still_come_out_whole() -> None:
    stats = run_benchmark(allow_crossings=False, gap_px=STROKE_GAPS_PX)
    record("non_crossing_with_gaps", stats.summary())

    assert stats.counted > 0
    assert stats.recall >= MIN_RECALL, stats.summary()
    assert stats.spurious_short == 0, stats.summary()
    assert stats.polylines_per_wall <= MAX_POLYLINES_PER_WALL, stats.summary()


@pytest.mark.parametrize("gap_px", [None, STROKE_GAPS_PX], ids=["solid", "gaps"])
def test_walls_crossed_by_t_junctions_come_out_as_one_segment(
    gap_px: tuple[int, int] | None,
) -> None:
    stats = run_t_junctions(gap_px)
    summary = stats.summary()
    record(f"t_junctions_{'gaps' if gap_px else 'solid'}", summary)

    assert stats.walls >= 100
    assert summary["segments_per_wall"] <= MAX_SEGMENTS_PER_WALL, summary
    assert summary["mean_longest_segment_recovery"] >= MIN_LONGEST_SEGMENT_RECOVERY, summary
    assert stats.shortest_polyline_mm >= SPURIOUS_SHORTER_THAN_MM, summary


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


def test_gaps_break_every_stroke_and_keep_the_truth() -> None:
    size = (1200, 900)
    solid = generate_sketch(4, 6, size, blur_sigma_px=0)
    broken = generate_sketch(4, 6, size, blur_sigma_px=0, gap_px=(3, 3))

    assert broken.segments == solid.segments
    assert broken.meta["gap_px"] == (3, 3)
    for segment in broken.segments:
        line = segment.line()
        samples = [line.interpolate(t / 200, normalized=True) for t in range(60, 141)]
        along = [int(broken.image[round(p.y), round(p.x)]) for p in samples]
        assert max(along) > 200  # paper shows through the gap, between 30 and 70 %


def test_t_junction_stems_start_on_their_bar_and_are_perpendicular() -> None:
    sketch = generate_t_junctions(2, 4, (2400, 1800))
    lines = [segment.line() for segment in sketch.segments]
    stems = 0
    for segment, line in zip(sketch.segments, lines, strict=True):
        bars = [o for o in lines if o is not line and o.distance(Point(segment.p0)) < 1e-6]
        if not bars:
            continue
        stems += 1
        (bx0, by0), (bx1, by1) = bars[0].coords
        dx, dy = segment.p1[0] - segment.p0[0], segment.p1[1] - segment.p0[1]
        assert abs(dx * (bx1 - bx0) + dy * (by1 - by0)) < 1e-6 * line.length * bars[0].length
    assert 4 <= stems <= 8 and len(sketch.segments) == 4 + stems
    again = generate_t_junctions(2, 4, (2400, 1800))
    assert np.array_equal(sketch.image, again.image) and sketch.segments == again.segments


def test_too_many_segments_for_the_sheet_is_an_error() -> None:
    with pytest.raises(ValueError, match="could not place"):
        generate_sketch(0, 200, (400, 300))
