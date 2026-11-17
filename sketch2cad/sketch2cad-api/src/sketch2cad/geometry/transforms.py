"""Scan scales (image→paper) and the paper→world transform (spec §11.1, §11.2).

Axes: image px and paper mm have Y down; world metres have Y up, so paper→world inverts Y:
``world = (x_mm × paper_scale / 1000, −y_mm × paper_scale / 1000)``. Scan calibrations are a
uniform ``mm_per_px`` that :func:`scale_matrix` turns into the same 3×3 image→paper form a
photo homography has, so ``calibration.image_to_paper`` always stores a 3×3 matrix.
"""

import math
from collections.abc import Sequence
from typing import NamedTuple, overload

import numpy as np

from sketch2cad.geometry.homography import Matrix3, Point, PointArray, TransformError, apply_h

MM_PER_INCH = 25.4


class SegmentScale(NamedTuple):
    """Scale from a reference segment and, when the file has a DPI, its signed difference.

    ``dpi_discrepancy_pct`` is ``(mm_per_px − 25.4 / dpi) / (25.4 / dpi) × 100``.
    """

    mm_per_px: float
    dpi_discrepancy_pct: float | None


def _positive(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number, got {value}")
    return float(value)


def scan_dpi_scale(dpi: float) -> float:
    """Paper millimetres per image pixel of a scan with ``dpi`` dots per inch (25.4 / dpi)."""
    return MM_PER_INCH / _positive(dpi, "dpi")


def discrepancy_pct(measured: float, expected: float) -> float:
    """Signed relative difference ``(measured − expected) / expected × 100``."""
    return (measured - expected) / _positive(expected, "expected") * 100.0


def scan_segment_scale(
    a_px: Point, b_px: Point, length_mm: float, dpi: float | None = None
) -> SegmentScale:
    """Millimetres per pixel from two image points ``length_mm`` apart on paper.

    With ``dpi`` the result also reports the difference with :func:`scan_dpi_scale`.
    """
    length = _positive(length_mm, "length_mm")
    if not all(math.isfinite(v) for v in (*a_px, *b_px)):
        raise ValueError("segment points must be finite")
    distance_px = math.dist(a_px, b_px)
    if distance_px == 0.0:
        raise ValueError("segment points coincide")
    mm_per_px = length / distance_px
    discrepancy = None if dpi is None else discrepancy_pct(mm_per_px, scan_dpi_scale(dpi))
    return SegmentScale(mm_per_px, discrepancy)


def scale_matrix(mm_per_px: float) -> Matrix3:
    """3×3 image→paper matrix of a uniform scan scale (no translation, Y stays down)."""
    k = _positive(mm_per_px, "mm_per_px")
    return np.diag([k, k, 1])


def paper_to_world_matrix(paper_scale: float) -> Matrix3:
    """3×3 paper mm → world m matrix for a 1:``paper_scale`` drawing, inverting Y."""
    k = _positive(paper_scale, "paper_scale") / 1000.0
    return np.diag([k, -k, 1])


def world_to_paper_matrix(paper_scale: float) -> Matrix3:
    """Inverse of :func:`paper_to_world_matrix`."""
    k = 1000.0 / _positive(paper_scale, "paper_scale")
    return np.diag([k, -k, 1])


def _map(matrix: Matrix3, points: Point | Sequence[Point] | PointArray) -> Point | PointArray:
    arr = np.asarray(points, dtype=np.float64)
    if arr.shape == (2,):
        return apply_h(matrix, (float(arr[0]), float(arr[1])))
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise TransformError(f"expected a point or an (N, 2) array, got shape {arr.shape}")
    return apply_h(matrix, arr)


@overload
def paper_to_world(mm: Point, paper_scale: float) -> Point: ...
@overload
def paper_to_world(mm: Sequence[Point] | PointArray, paper_scale: float) -> PointArray: ...
def paper_to_world(
    mm: Point | Sequence[Point] | PointArray, paper_scale: float
) -> Point | PointArray:
    """Paper mm (Y down) to world metres (Y up): ``m = mm × paper_scale / 1000``, Y negated."""
    return _map(paper_to_world_matrix(paper_scale), mm)


@overload
def world_to_paper(m: Point, paper_scale: float) -> Point: ...
@overload
def world_to_paper(m: Sequence[Point] | PointArray, paper_scale: float) -> PointArray: ...
def world_to_paper(
    m: Point | Sequence[Point] | PointArray, paper_scale: float
) -> Point | PointArray:
    """World metres (Y up) to paper mm (Y down); inverse of :func:`paper_to_world`."""
    return _map(world_to_paper_matrix(paper_scale), m)
