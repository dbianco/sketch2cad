"""Image-to-paper homography (spec §11.1, §11.2 ``photo_homography_4pt``).

Pure functions on 3×3 ``float64`` matrices. Image pixels and paper millimetres both have
Y pointing down. A homography maps image px to paper mm; :func:`homography_to_json` and
:func:`homography_from_json` give the ``calibration.image_to_paper`` JSON form (a list of
three rows of three numbers).
"""

from collections.abc import Sequence
from typing import Any, NamedTuple, overload

import cv2
import numpy as np
import numpy.typing as npt

Point = tuple[float, float]
PointArray = npt.NDArray[np.float64]
"""Batch of points, shape ``(N, 2)``."""
Matrix3 = npt.NDArray[np.float64]
"""3×3 projective matrix normalised so that ``m[2, 2] == 1``."""

_COLLINEAR_RTOL = 1e-9
"""Relative numerical tolerance: twice the triangle area over the squared extent."""
_W_EPS = 1e-12
"""Homogeneous ``w`` below this magnitude means the point maps to infinity."""
_EPS = float(np.finfo(np.float64).eps)


class DegenerateControlPointsError(ValueError):
    """The control points cannot define a homography (duplicate, collinear, non-convex)."""


class TransformError(ValueError):
    """A matrix or a point cannot be used by a transform (singular, not finite, at infinity)."""


class ReprojectionError(NamedTuple):
    """Distances in paper mm between mapped image points and their expected paper points."""

    max_mm: float
    rms_mm: float

    def to_json(self) -> dict[str, float]:
        """JSON form of ``calibration.reprojection_error_mm``."""
        return {"max": self.max_mm, "rms": self.rms_mm}


def rectangle_corners_mm(width_mm: float, height_mm: float) -> tuple[Point, Point, Point, Point]:
    """Paper corners of a ``width_mm`` × ``height_mm`` rectangle: TL, TR, BR, BL (Y down)."""
    if not (np.isfinite(width_mm) and np.isfinite(height_mm)) or width_mm <= 0 or height_mm <= 0:
        raise DegenerateControlPointsError(
            f"rectangle dimensions must be positive, got {width_mm} x {height_mm} mm"
        )
    w, h = float(width_mm), float(height_mm)
    return ((0.0, 0.0), (w, 0.0), (w, h), (0.0, h))


def _as_quad(points: Sequence[Point] | PointArray, label: str) -> PointArray:
    arr = np.asarray(points, dtype=np.float64)
    if arr.shape != (4, 2):
        raise DegenerateControlPointsError(
            f"{label}: expected 4 points of 2 coordinates, got shape {arr.shape}"
        )
    if not np.all(np.isfinite(arr)):
        raise DegenerateControlPointsError(f"{label}: coordinates must be finite")
    return arr


def _check_quad(quad: PointArray, label: str) -> None:
    """Reject duplicate, collinear and non-convex (wrongly ordered) quadrilaterals."""
    extent = float(np.max(np.ptp(quad, axis=0)))
    if extent == 0.0:
        raise DegenerateControlPointsError(f"{label}: all points coincide")
    for i in range(4):
        for j in range(i + 1, 4):
            if np.array_equal(quad[i], quad[j]):
                raise DegenerateControlPointsError(f"{label}: points {i} and {j} are duplicated")
    crosses: list[float] = []
    for i in range(4):
        a, b, c = quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]
        cross = float((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
        if abs(cross) <= _COLLINEAR_RTOL * extent * extent:
            raise DegenerateControlPointsError(
                f"{label}: points {i}, {(i + 1) % 4} and {(i + 2) % 4} are collinear"
            )
        crosses.append(cross)
    if not (all(c > 0 for c in crosses) or all(c < 0 for c in crosses)):
        raise DegenerateControlPointsError(
            f"{label}: points do not form a convex quadrilateral in order"
        )


def homography_from_4_points(
    image_px: Sequence[Point] | PointArray, paper_mm: Sequence[Point] | PointArray
) -> Matrix3:
    """Exact homography mapping 4 image points (px) to 4 paper points (mm).

    Points correspond by index and must go around the quadrilateral in order (for a
    rectangle use :func:`rectangle_corners_mm`). Raises :class:`DegenerateControlPointsError`
    for anything that cannot define a proper homography.
    """
    src = _as_quad(image_px, "image points")
    dst = _as_quad(paper_mm, "paper points")
    _check_quad(src, "image points")
    _check_quad(dst, "paper points")
    matrix, _ = cv2.findHomography(src, dst, method=0)
    if matrix is None:  # pyright: ignore[reportUnnecessaryComparison]
        raise DegenerateControlPointsError("OpenCV could not compute a homography")
    return _normalised(np.asarray(matrix, dtype=np.float64))


def _normalised(matrix: Matrix3) -> Matrix3:
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise TransformError(f"expected a finite 3x3 matrix, got shape {matrix.shape}")
    if not np.isfinite(np.linalg.cond(matrix)) or np.linalg.cond(matrix) > 1 / _EPS:
        raise TransformError("matrix is singular")
    if abs(float(matrix[2, 2])) < _W_EPS:
        raise TransformError("matrix maps the origin to infinity and cannot be normalised")
    return matrix / matrix[2, 2]


def invert_h(h: Matrix3) -> Matrix3:
    """Inverse homography (paper mm to image px for an image-to-paper matrix)."""
    return _normalised(np.linalg.inv(_normalised(np.asarray(h, dtype=np.float64))))


def _apply(h: Matrix3, points: Point | Sequence[Point] | PointArray) -> Point | PointArray:
    arr = np.asarray(points, dtype=np.float64)
    single = arr.shape == (2,)
    batch = arr.reshape(1, 2) if single else arr
    if batch.ndim != 2 or batch.shape[1] != 2:
        raise TransformError(f"expected a point or an (N, 2) array, got shape {arr.shape}")
    homogeneous = batch @ h[:, :2].T + h[:, 2]
    w = homogeneous[:, 2]
    if np.any(np.abs(w) < _W_EPS):
        raise TransformError("a point maps to infinity under this homography")
    mapped = homogeneous[:, :2] / w[:, None]
    if single:
        return (float(mapped[0, 0]), float(mapped[0, 1]))
    return mapped


@overload
def apply_h(h: Matrix3, points: Point) -> Point: ...
@overload
def apply_h(h: Matrix3, points: Sequence[Point] | PointArray) -> PointArray: ...
def apply_h(h: Matrix3, points: Point | Sequence[Point] | PointArray) -> Point | PointArray:
    """Map one point (returns a tuple) or a batch (returns an ``(N, 2)`` array) through ``h``."""
    return _apply(np.asarray(h, dtype=np.float64), points)


@overload
def apply_h_inverse(h: Matrix3, points: Point) -> Point: ...
@overload
def apply_h_inverse(h: Matrix3, points: Sequence[Point] | PointArray) -> PointArray: ...
def apply_h_inverse(h: Matrix3, points: Point | Sequence[Point] | PointArray) -> Point | PointArray:
    """Map one point or a batch through the inverse of ``h``."""
    return _apply(invert_h(h), points)


def reprojection_error(
    h: Matrix3, image_px: Sequence[Point] | PointArray, paper_mm: Sequence[Point] | PointArray
) -> ReprojectionError:
    """Max and RMS distance (mm) between ``apply_h(h, image_px)`` and ``paper_mm``.

    Used for the control points and for the extra check points the technician marks
    (spec §11.2: they are reported but do not take part in the fit).
    """
    src = np.asarray(image_px, dtype=np.float64)
    dst = np.asarray(paper_mm, dtype=np.float64)
    if src.ndim != 2 or src.shape[1:] != (2,) or src.shape != dst.shape or len(src) == 0:
        raise TransformError(
            f"expected two non-empty (N, 2) arrays of equal shape, got {src.shape} and {dst.shape}"
        )
    distances = np.linalg.norm(apply_h(h, src) - dst, axis=1)
    return ReprojectionError(float(distances.max()), float(np.sqrt(np.mean(distances**2))))


def homography_to_json(h: Matrix3) -> list[list[float]]:
    """JSON form of a 3×3 matrix (``calibration.image_to_paper``): three rows of floats."""
    return [[float(v) for v in row] for row in _normalised(np.asarray(h, dtype=np.float64))]


def homography_from_json(data: Any) -> Matrix3:
    """Parse :func:`homography_to_json` output; raises :class:`TransformError` if invalid."""
    try:
        matrix = np.asarray(data, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise TransformError("matrix JSON must be three rows of three numbers") from exc
    return _normalised(matrix)
