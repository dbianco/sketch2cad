"""``suggest_lines`` vision pipeline (REQ-17; spec section 10).

gray -> ``cv2.adaptiveThreshold`` (Gaussian) -> morphological opening -> ``skeletonize``
-> skeleton graph -> paths between nodes (collinear branches joined) -> shapely simplify
-> drop short paths -> confidence. Pure and local: no I/O, no database, no external AI
service. The input is the rectified image (:data:`PX_PER_MM` px per paper mm) and the
output stays in its pixels: ``(x, y)`` = (column, row), integers at pixel centres, so
paper mm = px / :data:`PX_PER_MM` (conversion to world metres is done by the caller).
Suggestions are always ``trace_only``: they are never metric.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

import numpy.typing as npt

from sketch2cad.calibration.rectify import PX_PER_MM
from sketch2cad.config_params import ParamValue, code_default
from sketch2cad.vision.binarize import gray_on_white, stroke_mask
from sketch2cad.vision.simplify import confidence, polyline_length, simplify_path, straightness
from sketch2cad.vision.skeleton import skeleton_paths

logger = logging.getLogger(__name__)

PARAM_PREFIX = "suggest_lines."

MAX_IMAGE_PIXELS = 100_000_000
"""Images larger than this (100 megapixels, an A0 sheet at 10 px/mm is 94) are refused."""

MAX_PATHS = 2000
"""At most this many polylines are returned: the highest-confidence ones."""

__all__ = [
    "MAX_IMAGE_PIXELS",
    "MAX_PATHS",
    "PX_PER_MM",
    "ImageTooLargeError",
    "SuggestParams",
    "SuggestedPolyline",
    "UnsupportedImageError",
    "VisionInputError",
    "suggest_polylines",
]


class VisionInputError(ValueError):
    """The image cannot be processed by the pipeline."""


class ImageTooLargeError(VisionInputError):
    """The image has more than :data:`MAX_IMAGE_PIXELS` pixels."""


class UnsupportedImageError(VisionInputError):
    """The array is not a gray, BGR or BGRA image of a numeric dtype."""


@dataclass(frozen=True)
class SuggestParams:
    """Parameters of the pipeline; field ``x`` is the configuration parameter
    ``suggest_lines.x`` and defaults to its code default."""

    threshold_block_size: int = int(code_default("suggest_lines.threshold_block_size"))
    threshold_c: float = float(code_default("suggest_lines.threshold_c"))
    opening_kernel_px: int = int(code_default("suggest_lines.opening_kernel_px"))
    simplify_tolerance_mm: float = float(code_default("suggest_lines.simplify_tolerance_mm"))
    min_length_mm: float = float(code_default("suggest_lines.min_length_mm"))
    merge_angle_deg: float = float(code_default("suggest_lines.merge_angle_deg"))

    def __post_init__(self) -> None:
        if self.threshold_block_size <= 1 or self.threshold_block_size % 2 == 0:
            raise ValueError("threshold_block_size must be odd and greater than 1")
        if self.opening_kernel_px < 1:
            raise ValueError("opening_kernel_px must be at least 1")
        if self.simplify_tolerance_mm <= 0 or self.min_length_mm <= 0:
            raise ValueError("simplify_tolerance_mm and min_length_mm must be positive")
        if not 0 < self.merge_angle_deg < 90:
            raise ValueError("merge_angle_deg must be between 0 and 90")

    @classmethod
    def param_names(cls) -> list[str]:
        """Configuration parameter names, e.g. for ``config_params.get_params``."""
        return [PARAM_PREFIX + field.name for field in fields(cls)]

    @classmethod
    def from_params(cls, values: Mapping[str, ParamValue]) -> "SuggestParams":
        """Parameters from resolved ``suggest_lines.*`` values; missing names keep defaults."""
        chosen: dict[str, Any] = {}
        for field in fields(cls):
            name = PARAM_PREFIX + field.name
            if name in values:
                chosen[field.name] = values[name]
        return cls(**chosen)


@dataclass(frozen=True)
class SuggestedPolyline:
    """A suggested polyline in rectified pixels; closed when the last point repeats the first."""

    points: tuple[tuple[float, float], ...]
    confidence: float
    length_mm: float


def suggest_polylines(
    image: npt.NDArray[Any], params: SuggestParams | None = None
) -> list[SuggestedPolyline]:
    """Suggested polylines of the strokes of a rectified sketch, sorted by their points.

    Blank or noisy images give few or no polylines (at most :data:`MAX_PATHS`). Raises
    :class:`ImageTooLargeError` above :data:`MAX_IMAGE_PIXELS` and
    :class:`UnsupportedImageError` for arrays that are not an image.
    """
    params = params or SuggestParams()
    if image.ndim >= 2 and image.shape[0] * image.shape[1] > MAX_IMAGE_PIXELS:
        raise ImageTooLargeError(f"image of {image.shape[1]}x{image.shape[0]} px is too large")
    if image.size == 0:
        return []
    try:
        gray = gray_on_white(image)
    except ValueError as exc:
        raise UnsupportedImageError(str(exc)) from None
    mask = stroke_mask(
        gray,
        block_size=params.threshold_block_size,
        c=params.threshold_c,
        opening_kernel_px=params.opening_kernel_px,
    )
    min_length_px = params.min_length_mm * PX_PER_MM
    paths = skeleton_paths(
        mask, min_length_px=min_length_px, merge_angle_deg=params.merge_angle_deg
    )
    found: list[SuggestedPolyline] = []
    for path in paths:
        points = simplify_path(path, params.simplify_tolerance_mm * PX_PER_MM)
        length_px = polyline_length(points)
        if length_px < min_length_px:
            continue
        length_mm = length_px / PX_PER_MM
        score = confidence(length_mm, straightness(path))
        found.append(SuggestedPolyline(tuple(points), score, length_mm))
    if len(found) > MAX_PATHS:
        logger.info("Keeping %d of %d suggested polylines", MAX_PATHS, len(found))
        found = sorted(found, key=lambda p: (-p.confidence, -p.length_mm, p.points))[:MAX_PATHS]
    return sorted(found, key=lambda p: p.points)
