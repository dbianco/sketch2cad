"""Stroke mask of a rectified sketch: gray conversion, adaptive threshold and opening.

First stage of ``suggest_lines`` (spec section 10). Dark strokes on light paper become the
``True`` foreground of a boolean mask.

Optional grid removal (``suggest_lines.remove_grid``): on a colour image, light pixels whose
darkening is mostly colour are taken as printed grid lines (graph paper) and replaced by
the surrounding paper before thresholding. A pixel is grid when its chroma
(``max(B, G, R) - min(B, G, R)``) is at least :data:`GRID_MIN_CHROMA` and at least
:data:`GRID_CHROMA_RATIO` times its darkness (``255 - gray``), and its gray is at least
:data:`GRID_MIN_GRAY` (dark ink, even coloured ink, is never removed). It is hue-agnostic
(orange, blue or green grids) but cannot see a grey grid, and gray input is unchanged.
"""

from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from sketch2cad.qc.checks import GrayImage, to_gray

Mask = npt.NDArray[np.bool_]

GRID_MIN_CHROMA = 8
"""Chroma (8-bit levels) below which a pixel is never grid: JPEG noise on neutral ink."""
GRID_CHROMA_RATIO = 0.3
"""A grid pixel's chroma is at least this fraction of its darkness: its colour, not ink."""
GRID_MIN_GRAY = 170
"""Pixels darker than this gray are ink and never grid (printed grids are light)."""
GRID_FILL_PX = 9
"""Side of the square neighbourhood whose lightest gray replaces a grid pixel (paper)."""


def gray_on_white(image: npt.NDArray[Any], *, remove_grid: bool = False) -> GrayImage:
    """8-bit gray version of a gray, BGR or BGRA image of 8 or 16 bits (or float).

    Transparent pixels of a BGRA image are composited over white paper, so they never
    become strokes. With ``remove_grid`` the coloured grid of an 8 or 16-bit colour image
    is replaced by paper (see the module docstring). Raises ``ValueError`` for shapes,
    channel counts or dtypes that are not an image (see :func:`sketch2cad.qc.checks.to_gray`).
    """
    if image.ndim == 3 and image.shape[2] == 4:
        gray = _colour_gray(image[:, :, :3], remove_grid).astype(np.uint16)
        alpha = to_gray(image[:, :, 3:4]).astype(np.uint16)
        composite = (gray * alpha + 255 * (255 - alpha) + 127) // 255
        return composite.astype(np.uint8)
    return _colour_gray(image, remove_grid)


def _colour_gray(image: npt.NDArray[Any], remove_grid: bool) -> GrayImage:
    gray = to_gray(image)
    if not remove_grid or image.ndim != 3 or image.shape[2] != 3:
        return gray
    if image.dtype == np.uint16:
        image = (image >> 8).astype(np.uint8)
    if image.dtype != np.uint8:
        return gray
    channels = image.astype(np.int16)
    chroma = channels.max(axis=2) - channels.min(axis=2)
    darkness = 255 - gray.astype(np.int16)
    grid = (
        (chroma >= GRID_MIN_CHROMA)
        & (chroma >= GRID_CHROMA_RATIO * darkness)
        & (gray >= GRID_MIN_GRAY)
    )
    if not grid.any():
        return gray
    paper = cv2.dilate(gray, np.ones((GRID_FILL_PX, GRID_FILL_PX), dtype=np.uint8))
    return np.where(grid, paper, gray).astype(np.uint8)


def stroke_mask(gray: GrayImage, *, block_size: int, c: float, opening_kernel_px: int) -> Mask:
    """Foreground mask of the dark strokes of ``gray``.

    ``cv2.adaptiveThreshold`` (Gaussian, ``THRESH_BINARY_INV``) marks pixels darker than
    their ``block_size`` neighbourhood mean minus ``c``; a ``opening_kernel_px`` square
    morphological opening then removes specks thinner than the kernel. ``block_size``
    must be odd and greater than 1 (``ValueError`` otherwise).
    """
    if block_size <= 1 or block_size % 2 == 0:
        raise ValueError(f"threshold block size must be odd and > 1, got {block_size}")
    if opening_kernel_px < 1:
        raise ValueError(f"opening kernel must be >= 1 px, got {opening_kernel_px}")
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block_size, c
    )
    kernel = np.ones((opening_kernel_px, opening_kernel_px), dtype=np.uint8)
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    return np.asarray(opened > 0, dtype=np.bool_)
