"""Image→paper and paper→world transforms (H2-1, CA-02, REQ-03, REQ-04)."""

import itertools
import json
import math
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sketch2cad.geometry import (
    DegenerateControlPointsError,
    ReprojectionError,
    TransformError,
    apply_h,
    apply_h_inverse,
    homography_from_4_points,
    homography_from_json,
    homography_to_json,
    invert_h,
    paper_to_world,
    paper_to_world_matrix,
    rectangle_corners_mm,
    reprojection_error,
    scale_matrix,
    scan_dpi_scale,
    scan_segment_scale,
    world_to_paper,
)

PAPER_W_MM, PAPER_H_MM = 200.0, 150.0
PX_PER_MM = 10  # rectified canvas resolution (spec §10 ``rectify``)
DOT_RADIUS_MM = 2.0
DOTS_MM = [(x, y) for x in (25.0, 62.5, 100.0, 137.5, 175.0) for y in (25.0, 75.0, 125.0)]
# Where the paper corners (TL, TR, BR, BL) land in the simulated 2400 x 1800 px photo.
PHOTO_SIZE = (2400, 1800)
PHOTO_CORNERS_PX = np.array([[420, 260], [2010, 380], [2150, 1600], [300, 1450]], np.float64)

Image = npt.NDArray[np.uint8]


def tolerance_mm(true_mm: float) -> float:
    return max(1.0, 0.01 * true_mm)


def flat_pattern() -> Image:
    """The 200 x 150 mm sheet at 10 px/mm: white paper with black dots at ``DOTS_MM``."""
    image = np.full((int(PAPER_H_MM) * PX_PER_MM, int(PAPER_W_MM) * PX_PER_MM), 255, np.uint8)
    for x, y in DOTS_MM:
        center = (round(x * PX_PER_MM), round(y * PX_PER_MM))
        cv2.circle(image, center, round(DOT_RADIUS_MM * PX_PER_MM), 0, -1, cv2.LINE_AA)
    return image


def dot_centroids(image: Image, min_area: int) -> npt.NDArray[np.float64]:
    """Centroids (px) of the dark blobs inside the bright sheet (the desk touches the frame)."""
    _, ink = cv2.threshold(image, 128, 255, cv2.THRESH_BINARY_INV)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    height, width = image.shape

    def inside(i: int) -> bool:
        x, y, w, h = (int(v) for v in stats[i, :4])
        return x > 0 and y > 0 and x + w < width and y + h < height

    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= min_area and inside(i)]
    return np.asarray(centroids[keep], dtype=np.float64)


def match_to_truth(paper_mm: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Truth dot for each measured point (nearest neighbour), asserting a 1:1 match."""
    truth = np.asarray(DOTS_MM, dtype=np.float64)
    nearest = [int(np.argmin(np.hypot(*(truth - p).T))) for p in paper_mm]
    assert sorted(nearest) == list(range(len(truth)))
    return truth[nearest]


def assert_distances_within_tolerance(
    measured_mm: npt.NDArray[np.float64], truth_mm: npt.NDArray[np.float64]
) -> None:
    for i, j in itertools.combinations(range(len(truth_mm)), 2):
        true = math.dist(truth_mm[i], truth_mm[j])
        got = math.dist(measured_mm[i], measured_mm[j])
        assert abs(got - true) <= tolerance_mm(true), (i, j, got, true)


@pytest.fixture(scope="module")
def photo() -> Image:
    """Deterministic synthetic photo: the pattern warped onto a dark desk by a homography."""
    paper_px = np.asarray(rectangle_corners_mm(PAPER_W_MM, PAPER_H_MM)) * PX_PER_MM
    true_h = cv2.getPerspectiveTransform(
        paper_px.astype(np.float32), PHOTO_CORNERS_PX.astype(np.float32)
    )
    return cv2.warpPerspective(flat_pattern(), true_h, PHOTO_SIZE, borderValue=(40,)).astype(
        np.uint8
    )


@pytest.fixture(scope="module")
def estimated_h() -> Any:
    """Homography from the 4 paper corners as clicked by the technician (whole pixels)."""
    clicked = np.round(PHOTO_CORNERS_PX + [[0.4, -0.3], [-0.2, 0.5], [0.3, 0.3], [-0.5, -0.1]])
    return homography_from_4_points(clicked, rectangle_corners_mm(PAPER_W_MM, PAPER_H_MM))


def test_synthetic_photo_rectifies_within_tolerance(photo: Image, estimated_h: Any) -> None:
    centroids_px = dot_centroids(photo, min_area=50)
    assert len(centroids_px) == len(DOTS_MM)
    measured_mm = apply_h(estimated_h, centroids_px)
    truth_mm = match_to_truth(measured_mm)
    assert_distances_within_tolerance(measured_mm, truth_mm)
    error = reprojection_error(estimated_h, centroids_px, truth_mm)
    assert error.max_mm <= 1.0
    assert error.rms_mm <= error.max_mm


def test_warp_to_rectified_canvas_restores_paper_distances(photo: Image, estimated_h: Any) -> None:
    to_canvas = scale_matrix(1 / PX_PER_MM)
    canvas_h = invert_h(to_canvas) @ estimated_h  # image px -> canvas px at 10 px/mm
    size = (int(PAPER_W_MM) * PX_PER_MM, int(PAPER_H_MM) * PX_PER_MM)
    rectified = cv2.warpPerspective(photo, canvas_h, size, borderValue=(255,)).astype(np.uint8)
    measured_mm = apply_h(to_canvas, dot_centroids(rectified, min_area=200))
    assert_distances_within_tolerance(measured_mm, match_to_truth(measured_mm))


def test_control_points_reproject_exactly() -> None:
    corners = rectangle_corners_mm(PAPER_W_MM, PAPER_H_MM)
    h = homography_from_4_points(PHOTO_CORNERS_PX, corners)
    error = reprojection_error(h, PHOTO_CORNERS_PX, corners)
    assert error.max_mm < 1e-9
    assert apply_h(h, (420.0, 260.0)) == pytest.approx((0.0, 0.0), abs=1e-9)


coordinate = st.floats(min_value=0.0, max_value=4000.0, allow_nan=False)
jitter = st.floats(min_value=-300.0, max_value=300.0, allow_nan=False)


@settings(max_examples=300, deadline=None)
@given(
    x=coordinate,
    y=coordinate,
    corner_jitter=st.lists(st.tuples(jitter, jitter), min_size=4, max_size=4),
)
def test_inverse_of_apply_h_is_identity(
    x: float, y: float, corner_jitter: list[tuple[float, float]]
) -> None:
    image_corners = np.array([[500, 400], [3500, 400], [3500, 2800], [500, 2800]], np.float64)
    h = homography_from_4_points(image_corners + corner_jitter, rectangle_corners_mm(200, 150))
    back = apply_h_inverse(h, apply_h(h, (x, y)))
    assert back == pytest.approx((x, y), abs=1e-6)


def test_apply_h_batch_matches_single_points(estimated_h: Any) -> None:
    points = [(0.0, 0.0), (1234.5, 678.9), (2000.0, 1500.0)]
    batch = apply_h(estimated_h, points)
    assert batch.shape == (3, 2)
    for row, point in zip(batch, points, strict=True):
        assert tuple(row) == pytest.approx(apply_h(estimated_h, point), abs=1e-12)
    assert apply_h_inverse(estimated_h, batch) == pytest.approx(np.array(points), abs=1e-6)


def test_paper_to_world_scales_and_inverts_y() -> None:
    assert paper_to_world((100, 50), 100) == pytest.approx((10.0, -5.0), abs=1e-9)
    assert world_to_paper((10.0, -5.0), 100) == pytest.approx((100.0, 50.0), abs=1e-9)
    # Spec §13: a 1 m line at 1:100 measures 10 mm on paper.
    assert world_to_paper((1.0, 0.0), 100) == pytest.approx((10.0, 0.0), abs=1e-9)
    batch = paper_to_world(np.array([[0.0, 0.0], [20.0, 10.0]]), 200)
    assert batch == pytest.approx(np.array([[0.0, 0.0], [4.0, -2.0]]), abs=1e-9)
    assert paper_to_world_matrix(100).tolist() == [[0.1, 0, 0], [0, -0.1, 0], [0, 0, 1]]


@pytest.mark.parametrize("paper_scale", [0, -100, math.nan, math.inf])
def test_paper_to_world_rejects_invalid_scale(paper_scale: float) -> None:
    with pytest.raises(ValueError, match="paper_scale"):
        paper_to_world((1.0, 1.0), paper_scale)


@pytest.mark.parametrize(
    ("dpi", "mm_per_px"), [(300, 25.4 / 300), (600, 25.4 / 600), (25.4, 1.0), (254, 0.1)]
)
def test_scan_dpi_scale(dpi: float, mm_per_px: float) -> None:
    assert scan_dpi_scale(dpi) == pytest.approx(mm_per_px, rel=1e-12)


@pytest.mark.parametrize("dpi", [0, -300, math.nan, math.inf])
def test_scan_dpi_scale_rejects_invalid_dpi(dpi: float) -> None:
    with pytest.raises(ValueError, match="dpi"):
        scan_dpi_scale(dpi)


def test_scan_segment_scale_from_a_100_mm_ruler() -> None:
    # Spec §6 flow A: a 100 mm ruler; at 300 dpi it spans 100 * 300 / 25.4 = 1181.10 px.
    span_px = 100 * 300 / 25.4
    a = (100.0, 200.0)
    b = (100.0 + 0.6 * span_px, 200.0 + 0.8 * span_px)  # drawn diagonally (3-4-5)
    result = scan_segment_scale(a, b, 100.0, dpi=300)
    assert result.mm_per_px == pytest.approx(25.4 / 300, rel=1e-12)
    assert result.dpi_discrepancy_pct == pytest.approx(0.0, abs=1e-9)
    assert apply_h(scale_matrix(result.mm_per_px), b) == pytest.approx(
        (b[0] * 25.4 / 300, b[1] * 25.4 / 300), rel=1e-12
    )


def test_scan_segment_scale_reports_signed_dpi_discrepancy() -> None:
    larger = scan_segment_scale((0.0, 0.0), (100 / (1.01 * 25.4 / 300), 0.0), 100.0, dpi=300)
    assert larger.dpi_discrepancy_pct == pytest.approx(1.0, abs=1e-9)
    smaller = scan_segment_scale((0.0, 0.0), (0.0, 100 / (0.98 * 25.4 / 300)), 100.0, dpi=300)
    assert smaller.dpi_discrepancy_pct == pytest.approx(-2.0, abs=1e-9)
    assert scan_segment_scale((0.0, 0.0), (1000.0, 0.0), 100.0).dpi_discrepancy_pct is None


@pytest.mark.parametrize(
    ("a", "b", "length"),
    [((5.0, 5.0), (5.0, 5.0), 100.0), ((0.0, 0.0), (10.0, 0.0), 0.0), ((0, 0), (math.nan, 0), 1)],
)
def test_scan_segment_scale_rejects_degenerate_input(
    a: tuple[float, float], b: tuple[float, float], length: float
) -> None:
    with pytest.raises(ValueError):
        scan_segment_scale(a, b, length)


RECT = rectangle_corners_mm(200, 150)


@pytest.mark.parametrize(
    ("image_px", "message"),
    [
        ([(0, 0), (100, 0), (100, 0), (0, 100)], "duplicated"),
        ([(0, 0), (50, 0), (100, 0), (0, 100)], "collinear"),
        ([(0, 0), (100, 100), (100, 0), (0, 100)], "convex"),
        ([(0, 0), (100, 0), (40, 30), (0, 100)], "convex"),
        ([(7, 7)] * 4, "coincide"),
        ([(0, 0), (100, 0), (100, 100)], "expected 4 points"),
        ([(0, 0), (100, 0), (100, math.inf), (0, 100)], "finite"),
    ],
)
def test_homography_rejects_degenerate_control_points(
    image_px: list[tuple[float, float]], message: str
) -> None:
    with pytest.raises(DegenerateControlPointsError, match=message):
        homography_from_4_points(image_px, RECT)


def test_homography_rejects_degenerate_paper_rectangle() -> None:
    with pytest.raises(DegenerateControlPointsError, match="positive"):
        rectangle_corners_mm(0, 150)
    with pytest.raises(DegenerateControlPointsError, match="paper points"):
        homography_from_4_points(PHOTO_CORNERS_PX, [(0, 0), (1, 1), (2, 2), (3, 3)])


def test_point_at_infinity_and_bad_shapes_raise_transform_error() -> None:
    h = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.001, 0.0, 1.0]])
    with pytest.raises(TransformError, match="infinity"):
        apply_h(h, (-1000.0, 5.0))
    with pytest.raises(TransformError, match="shape"):
        apply_h(h, np.zeros((3, 3)))
    with pytest.raises(TransformError, match="singular"):
        invert_h(np.ones((3, 3)))
    with pytest.raises(TransformError):
        reprojection_error(h, [(0.0, 0.0)], [(0.0, 0.0), (1.0, 1.0)])


def test_homography_json_round_trip(estimated_h: Any) -> None:
    payload = json.loads(json.dumps(homography_to_json(estimated_h)))
    assert len(payload) == 3 and all(len(row) == 3 for row in payload)
    assert payload[2][2] == 1.0
    assert np.array_equal(homography_from_json(payload), estimated_h)
    error = ReprojectionError(0.4, 0.25)
    assert error.to_json() == {"max": 0.4, "rms": 0.25}
    for bad in ([[1, 2], [3, 4]], "matrix", [[1, 0, 0], [0, 1, 0], [0, 0, math.nan]]):
        with pytest.raises(TransformError):
            homography_from_json(bad)
