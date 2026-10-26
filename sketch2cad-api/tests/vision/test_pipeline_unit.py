"""Unit tests of the suggest_lines vision pipeline on synthetic rectified sketches.

Images are at 10 px per paper mm, so 2 mm of paper is 20 px. Strokes are dark 3 px lines
on white paper drawn with OpenCV (``(x, y)`` points), with light Gaussian noise.
"""

import math
import time
from collections.abc import Sequence

import cv2
import numpy as np
import numpy.typing as npt
import pytest

import sketch2cad.vision as vision
from sketch2cad.config_params import PARAMETERS
from sketch2cad.vision import (
    MAX_IMAGE_PIXELS,
    PX_PER_MM,
    ImageTooLargeError,
    SuggestedPolyline,
    SuggestParams,
    UnsupportedImageError,
    suggest_polylines,
)

Image = npt.NDArray[np.uint8]
Point = tuple[float, float]
Segment = tuple[Point, Point]

INK = 40
TOLERANCE_PX = 2.0 * PX_PER_MM  # 2 mm of paper
THREE_SEGMENTS: list[Segment] = [
    ((200.0, 300.0), (1500.0, 300.0)),  # horizontal, 130 mm
    ((1800.0, 200.0), (1800.0, 1300.0)),  # vertical, 110 mm
    ((300.0, 600.0), (1200.0, 1300.0)),  # diagonal, 114 mm
]


def paper(width: int = 2000, height: int = 1500, *, noise: float = 3.0, seed: int = 7) -> Image:
    """White gray paper of ``width`` x ``height`` px with light Gaussian noise."""
    rng = np.random.default_rng(seed)
    sheet = 245.0 + rng.normal(0.0, noise, (height, width))
    return np.clip(sheet, 0, 255).astype(np.uint8)


def draw(image: Image, segments: Sequence[Segment], thickness: int = 3) -> Image:
    for (x0, y0), (x1, y1) in segments:
        cv2.line(image, (round(x0), round(y0)), (round(x1), round(y1)), INK, thickness)
    return image


def point_segment_distance(point: Point, segment: Segment) -> float:
    (ax, ay), (bx, by) = segment
    px, py = point
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(point, (ax + t * dx, ay + t * dy))


def max_deviation(polyline: SuggestedPolyline, segment: Segment) -> float:
    return max(point_segment_distance(p, segment) for p in polyline.points)


def endpoint_error(polyline: SuggestedPolyline, segment: Segment) -> float:
    first, last = polyline.points[0], polyline.points[-1]
    a, b = segment
    return min(
        max(math.dist(first, a), math.dist(last, b)), max(math.dist(first, b), math.dist(last, a))
    )


def assert_invariants(result: list[SuggestedPolyline], min_length_mm: float = 5.0) -> None:
    assert len(result) <= vision.MAX_PATHS
    for polyline in result:
        assert 0.0 <= polyline.confidence <= 1.0
        assert polyline.length_mm >= min_length_mm
        assert len(polyline.points) >= 2
    assert result == sorted(result, key=lambda p: p.points)


def test_three_known_segments_give_three_polylines_within_2_mm() -> None:
    result = suggest_polylines(draw(paper(), THREE_SEGMENTS))

    assert len(result) == 3
    for segment in THREE_SEGMENTS:
        polyline = min(result, key=lambda p: max_deviation(p, segment))
        assert max_deviation(polyline, segment) < TOLERANCE_PX
        assert endpoint_error(polyline, segment) < TOLERANCE_PX
    assert_invariants(result)


def test_polylines_are_at_least_5_mm_and_confidence_in_unit_interval() -> None:
    lengths_mm = [math.dist(a, b) / PX_PER_MM for a, b in THREE_SEGMENTS]
    result = suggest_polylines(draw(paper(), THREE_SEGMENTS))

    assert_invariants(result)
    assert sorted(p.length_mm for p in result) == pytest.approx(sorted(lengths_mm), abs=1.0)
    assert all(p.confidence > 0.9 for p in result)  # long straight strokes


def test_stroke_shorter_than_min_length_is_dropped() -> None:
    short = ((400.0, 1000.0), (430.0, 1000.0))  # 3 mm
    result = suggest_polylines(draw(paper(), [THREE_SEGMENTS[0], short]))

    assert len(result) == 1
    assert max_deviation(result[0], THREE_SEGMENTS[0]) < TOLERANCE_PX


def test_collinear_segments_with_a_gap_stay_two_polylines() -> None:
    # Gaps are never bridged: the pipeline only traces what is drawn.
    left, right = ((200.0, 500.0), (900.0, 500.0)), ((930.0, 500.0), (1600.0, 500.0))
    result = suggest_polylines(draw(paper(), [left, right]))

    assert len(result) == 2
    assert max_deviation(result[0], left) < TOLERANCE_PX
    assert max_deviation(result[1], right) < TOLERANCE_PX


def test_l_shape_is_one_polyline_with_a_corner_vertex() -> None:
    corner = (1200.0, 300.0)
    arms = [((300.0, 300.0), corner), (corner, (1200.0, 1100.0))]
    result = suggest_polylines(draw(paper(), arms))

    assert len(result) == 1
    points = result[0].points
    assert len(points) == 3
    assert math.dist(points[1], corner) < TOLERANCE_PX
    assert all(min(max_deviation_point(p, arms)) < TOLERANCE_PX for p in points)


def max_deviation_point(point: Point, segments: Sequence[Segment]) -> list[float]:
    return [point_segment_distance(point, segment) for segment in segments]


def test_crossing_strokes_merge_collinear_branches() -> None:
    horizontal, vertical = ((200.0, 700.0), (1800.0, 700.0)), ((1000.0, 100.0), (1000.0, 1400.0))
    result = suggest_polylines(draw(paper(), [horizontal, vertical]))

    assert len(result) == 2
    for segment in (horizontal, vertical):
        polyline = min(result, key=lambda p: max_deviation(p, segment))
        assert endpoint_error(polyline, segment) < TOLERANCE_PX


def test_t_junction_keeps_the_bar_whole_and_the_stem_separate() -> None:
    bar, stem = ((200.0, 400.0), (1800.0, 400.0)), ((1000.0, 400.0), (1000.0, 1200.0))
    result = suggest_polylines(draw(paper(), [bar, stem]))

    assert len(result) == 2
    assert any(endpoint_error(p, bar) < TOLERANCE_PX for p in result)
    assert any(max_deviation(p, stem) < TOLERANCE_PX for p in result)


def test_closed_rectangle_is_one_closed_polyline() -> None:
    image = paper()
    cv2.rectangle(image, (300, 300), (1300, 900), INK, 3)
    result = suggest_polylines(image)

    assert len(result) == 1
    polyline = result[0]
    assert polyline.points[0] == polyline.points[-1]
    assert polyline.length_mm == pytest.approx(320.0, abs=2.0)
    assert polyline.confidence <= 0.5  # straightness of a closed path is 0


@pytest.mark.parametrize(
    "image",
    [
        np.full((1500, 2000), 255, dtype=np.uint8),
        np.zeros((1500, 2000), dtype=np.uint8),
        paper(),
        np.zeros((0, 0), dtype=np.uint8),
    ],
    ids=["white", "black", "paper-noise", "empty"],
)
def test_blank_images_give_no_polylines(image: Image) -> None:
    assert suggest_polylines(image) == []


def test_salt_and_pepper_noise_does_not_crash() -> None:
    rng = np.random.default_rng(3)
    image = draw(paper(), THREE_SEGMENTS)
    noise = rng.random(image.shape)
    image[noise < 0.1] = 0
    image[noise > 0.9] = 255

    assert_invariants(suggest_polylines(image))


def test_blob_noise_does_not_crash_and_respects_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(5)
    field = cv2.GaussianBlur(rng.random((1500, 2000)).astype(np.float32), (0, 0), 4)
    image = np.where(field < np.median(field), 0, 255).astype(np.uint8)
    uncapped = suggest_polylines(image)
    assert len(uncapped) > 10
    assert_invariants(uncapped)

    monkeypatch.setattr(vision, "MAX_PATHS", 10)
    capped = suggest_polylines(image)
    assert len(capped) == 10
    best = sorted(uncapped, key=lambda p: (-p.confidence, -p.length_mm, p.points))[:10]
    assert capped == sorted(best, key=lambda p: p.points)


def test_same_input_gives_the_same_output() -> None:
    image = draw(paper(), THREE_SEGMENTS)
    first = suggest_polylines(image)
    second = suggest_polylines(image.copy())

    assert first == second


def test_color_alpha_and_16_bit_inputs_match_gray() -> None:
    gray = draw(paper(), THREE_SEGMENTS)
    expected = suggest_polylines(gray)
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    bgra = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGRA)
    deep = gray.astype(np.uint16) * 257

    assert suggest_polylines(bgr) == expected
    assert suggest_polylines(bgra) == expected
    assert suggest_polylines(deep) == expected
    assert suggest_polylines(gray[:, :, np.newaxis]) == expected


def test_transparent_pixels_count_as_white_paper() -> None:
    image = cv2.cvtColor(draw(paper(), THREE_SEGMENTS), cv2.COLOR_GRAY2BGRA)
    image[:, :1000, 3] = 0  # hides the left half, where only the diagonal is complete

    result = suggest_polylines(image)

    assert all(x >= 990 for p in result for x, _ in p.points)


def test_params_override_changes_the_result() -> None:
    image = draw(paper(), THREE_SEGMENTS)
    params = SuggestParams.from_params({"suggest_lines.min_length_mm": 112.0})

    result = suggest_polylines(image, params)

    assert params.min_length_mm == 112.0
    assert params.threshold_block_size == 31  # unspecified names keep the default
    assert len(result) == 2
    assert_invariants(result, min_length_mm=112.0)


def test_default_params_follow_the_configuration_registry() -> None:
    assert set(SuggestParams.param_names()) == {
        name for name in PARAMETERS if name.startswith("suggest_lines.")
    }
    assert SuggestParams() == SuggestParams(31, 10.0, 3, 0.8, 5.0, 5.0)
    assert SuggestParams.from_params({}) == SuggestParams()


@pytest.mark.parametrize(
    "overrides",
    [
        {"threshold_block_size": 30},
        {"threshold_block_size": 1},
        {"opening_kernel_px": 0},
        {"min_length_mm": 0.0},
        {"merge_angle_deg": 90.0},
    ],
)
def test_invalid_params_are_refused(overrides: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        SuggestParams(**overrides)  # type: ignore[arg-type]


def test_oversized_image_is_refused() -> None:
    side = math.isqrt(MAX_IMAGE_PIXELS) + 1
    huge = np.broadcast_to(np.uint8(255), (side, side))  # a view: no memory allocated

    with pytest.raises(ImageTooLargeError):
        suggest_polylines(huge)


@pytest.mark.parametrize(
    "image",
    [
        np.zeros(100, dtype=np.uint8),
        np.zeros((100, 100, 5), dtype=np.uint8),
        np.zeros((100, 100), dtype=np.bool_),
    ],
    ids=["1d", "five-channels", "bool"],
)
def test_arrays_that_are_not_images_are_refused(image: npt.NDArray[np.generic]) -> None:
    with pytest.raises(UnsupportedImageError):
        suggest_polylines(image)


@pytest.mark.perf
def test_thirty_stroke_sketch_runs_in_reasonable_time() -> None:
    rng = np.random.default_rng(11)
    segments: list[Segment] = []
    for _ in range(30):
        x0, y0 = rng.uniform(100, 2900), rng.uniform(100, 2000)
        angle, length = rng.uniform(0, math.pi), rng.uniform(100, 1200)
        x1 = min(2900.0, max(100.0, x0 + length * math.cos(angle)))
        y1 = min(2000.0, max(100.0, y0 + length * math.sin(angle)))
        segments.append(((x0, y0), (x1, y1)))
    image = cv2.cvtColor(draw(paper(3000, 2100), segments), cv2.COLOR_GRAY2BGR)

    start = time.perf_counter()
    result = suggest_polylines(image)
    elapsed = time.perf_counter() - start

    print(f"suggest_polylines 3000x2100 with 30 strokes: {elapsed:.3f} s")
    assert_invariants(result)
    assert result
    assert elapsed < 30.0  # sanity bound only; measured ~0.2 s on an M4 Pro
