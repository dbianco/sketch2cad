"""Coordinates of ``suggest_lines`` output: rectified px -> paper mm -> local metres (spec 10).

Pure: no I/O and no database. The rectified canvas of every calibration method has paper
(0, 0) at pixel (0, 0): :func:`sketch2cad.calibration.rectify.rectify_image` warps with
``diag(px_per_mm, px_per_mm, 1) @ image_to_paper``, a homography for
``photo_homography_4pt`` (paper origin = first corner of the known rectangle) and a pure
scale with no translation for ``scan_dpi`` and ``scan_segment``. So, with no offset:

- paper mm = px / ``px_per_mm`` (10, both axes, Y down);
- world m = ``(x_mm * s / 1000, -y_mm * s / 1000)`` with ``s`` the plan's paper scale, or
  the provisional 1:100 when it has none (``schematic_sketch``);
- local m = the world point in the plan's local frame (identity for the default frame).

``image_region.polygon_px`` maps paper mm back to the original image through the inverse
of ``image_to_paper``, so the region points at the pixels the suggestion came from.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from sketch2cad.calibration.rectify import PX_PER_MM
from sketch2cad.geometry.frame import DEFAULT_FRAME, LocalFrame, rigid_between, world_frame
from sketch2cad.geometry.homography import Matrix3, Point, apply_h
from sketch2cad.geometry.transforms import paper_to_world

PROVISIONAL_PAPER_SCALE = 100
"""Scale of a plan without a declared one (``schematic_sketch``, spec section 10).

Same value as :data:`sketch2cad.plans.local_frame.PROVISIONAL_PAPER_SCALE`; repeated here so
this module stays free of the database layer (a test keeps both equal).
"""

MIN_CLOSED_VERTICES = 3


@dataclass(frozen=True)
class SuggestionFrame:
    """What the conversion needs from the plan's active calibration.

    ``paper_scale`` is the calibration's (``None`` gives the provisional 1:100),
    ``local_frame`` its stored frame (``None`` is the default frame) and ``image_to_paper``
    the 3x3 matrix of the original image (scale matrix for ``none``).
    """

    paper_scale: int | None
    local_frame: LocalFrame | None
    image_to_paper: Matrix3
    px_per_mm: float = PX_PER_MM

    @property
    def scale(self) -> int:
        return self.paper_scale or PROVISIONAL_PAPER_SCALE


def px_to_paper_mm(points: Sequence[Point], px_per_mm: float = PX_PER_MM) -> list[Point]:
    """Rectified pixels ``(x=col, y=row)`` to paper mm (Y down): divide by ``px_per_mm``."""
    return [(float(x) / px_per_mm, float(y) / px_per_mm) for x, y in points]


def paper_mm_to_local_m(
    points_mm: Sequence[Point], paper_scale: float, frame: LocalFrame | None = None
) -> list[Point]:
    """Paper mm to metres in ``frame`` (the default frame when ``None``), Y up."""
    if not points_mm:
        return []
    world = paper_to_world(np.asarray(points_mm, dtype=np.float64), paper_scale)
    pairs = [(float(x), float(y)) for x, y in world]
    if frame is None or frame.same_placement(DEFAULT_FRAME):
        return pairs
    transform = rigid_between(
        world_frame(DEFAULT_FRAME, paper_scale), world_frame(frame, paper_scale)
    )
    return transform.apply_points(pairs)


def paper_mm_to_image_px(points_mm: Sequence[Point], image_to_paper: Matrix3) -> list[Point]:
    """Paper mm back to pixels of the original image (inverse of ``image_to_paper``)."""
    if not points_mm:
        return []
    inverse = np.linalg.inv(np.asarray(image_to_paper, dtype=np.float64))
    mapped = apply_h(inverse, np.asarray(points_mm, dtype=np.float64))
    return [(float(x), float(y)) for x, y in mapped]


def polyline_geometry(coords_m: Sequence[Point]) -> dict[str, Any] | None:
    """``polyline`` geometry JSON; ``None`` when fewer than 2 distinct vertices remain.

    A path whose last point repeats the first is ``closed`` (the repeat dropped) when it
    keeps at least 3 vertices; otherwise it stays open. Consecutive duplicates are removed.
    """
    vertices: list[Point] = []
    for point in coords_m:
        if not all(math.isfinite(v) for v in point):
            return None
        if not vertices or vertices[-1] != point:
            vertices.append(point)
    closed = len(vertices) > MIN_CLOSED_VERTICES and vertices[0] == vertices[-1]
    if closed:
        vertices.pop()
    if len(vertices) < 2:
        return None
    return {"kind": "polyline", "coords_m": [[x, y] for x, y in vertices], "closed": closed}


@dataclass(frozen=True)
class ConvertedSuggestion:
    """Geometry in local metres and the image region of one suggested polyline."""

    geometry: dict[str, Any]
    polygon_px: list[list[float]]


def convert_polyline(
    points_px: Sequence[Point], frame: SuggestionFrame
) -> ConvertedSuggestion | None:
    """One pipeline polyline in rectified px to its entity geometry and image region."""
    paper = px_to_paper_mm(points_px, frame.px_per_mm)
    geometry = polyline_geometry(paper_mm_to_local_m(paper, frame.scale, frame.local_frame))
    if geometry is None:
        return None
    original = paper_mm_to_image_px(paper, frame.image_to_paper)
    return ConvertedSuggestion(geometry, [[round(x, 1), round(y, 1)] for x, y in original])
