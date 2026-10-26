"""Stroke mask of a rectified sketch: gray conversion, adaptive threshold and opening.

First stage of ``suggest_lines`` (spec section 10). Dark strokes on light paper become the
``True`` foreground of a boolean mask.
"""

from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from sketch2cad.qc.checks import GrayImage, to_gray

Mask = npt.NDArray[np.bool_]


def gray_on_white(image: npt.NDArray[Any]) -> GrayImage:
    """8-bit gray version of a gray, BGR or BGRA image of 8 or 16 bits (or float).

    Transparent pixels of a BGRA image are composited over white paper, so they never
    become strokes. Raises ``ValueError`` for shapes, channel counts or dtypes that are not
    an image (see :func:`sketch2cad.qc.checks.to_gray`).
    """
    if image.ndim == 3 and image.shape[2] == 4:
        gray = to_gray(image[:, :, :3]).astype(np.uint16)
        alpha = to_gray(image[:, :, 3:4]).astype(np.uint16)
        composite = (gray * alpha + 255 * (255 - alpha) + 127) // 255
        return composite.astype(np.uint8)
    return to_gray(image)


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
