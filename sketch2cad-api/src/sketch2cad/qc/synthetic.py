"""Synthetic sketch photos generated with OpenCV, used to test the quality checks.

Images are deterministic BGR ``uint8`` arrays: a white sheet with a hand-drawn-looking
floor plan (walls, a triangulation and dimension texts) lying on a dark desk.
"""

import cv2
import numpy as np
import numpy.typing as npt

from sketch2cad.qc.checks import laplacian_variance, to_gray

BgrImage = npt.NDArray[np.uint8]

DESK_BGR = (45, 50, 55)
PAPER_BGR = (235, 238, 240)
INK_BGR = (40, 35, 30)
SQRT2 = 2**0.5


def sketch(
    short_side: int, *, aspect: float = SQRT2, margin: float = 0.06, paper_inside: bool = True
) -> BgrImage:
    """A sharp sketch photo whose shorter side is ``short_side`` px (portrait, A-series ratio).

    With ``paper_inside`` the sheet leaves a ``margin`` of desk on every side; otherwise the
    sheet runs off the right and bottom edges of the frame.
    """
    height, width = round(short_side * aspect), short_side
    image = np.full((height, width, 3), DESK_BGR, dtype=np.uint8)
    pad_x, pad_y = round(width * margin), round(height * margin)
    right, bottom = (width - pad_x, height - pad_y) if paper_inside else (width, height)
    cv2.rectangle(image, (pad_x, pad_y), (right, bottom), PAPER_BGR, thickness=-1)
    _draw_plan(image, (pad_x, pad_y, right - pad_x, bottom - pad_y))
    return image


def _draw_plan(image: BgrImage, sheet: tuple[int, int, int, int]) -> None:
    x0, y0, w, h = sheet
    thick = max(2, round(min(w, h) / 400))
    rng = np.random.default_rng(37)
    step_x, step_y = w // 8, h // 10
    for row in range(1, 9):
        for col in range(1, 7):
            if rng.random() < 0.5:
                continue
            x, y = x0 + col * step_x, y0 + row * step_y
            cv2.rectangle(image, (x, y), (x + step_x, y + step_y), INK_BGR, thick)
            cv2.line(image, (x, y + step_y), (x + step_x, y), INK_BGR, max(1, thick // 2))
            label = f"{rng.uniform(1, 9):.2f}"
            scale = step_x / 160
            origin = (x + step_x // 10, y + step_y // 2)
            cv2.putText(image, label, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, INK_BGR, thick)


def blurred(image: BgrImage, sigma: float) -> BgrImage:
    """Gaussian blur of ``image`` with standard deviation ``sigma`` px (no-op for 0)."""
    if sigma <= 0:
        return image.copy()
    return cv2.GaussianBlur(image, (0, 0), sigmaX=sigma).astype(np.uint8)


def with_laplacian_variance(
    image: BgrImage, target: float, *, tolerance: float = 1.0, max_sigma: float = 40.0
) -> BgrImage:
    """Blur ``image`` until the Laplacian variance of its gray version is ``target`` +- tolerance.

    Bisects the blur sigma (variance falls as sigma grows). Raises ``ValueError`` when the
    sharp image is already below ``target`` or no sigma up to ``max_sigma`` reaches it.
    """
    if laplacian_variance(to_gray(image)) < target:
        raise ValueError("image is already below the target Laplacian variance")
    low, high = 0.0, max_sigma
    for _ in range(40):
        sigma = (low + high) / 2
        candidate = blurred(image, sigma)
        value = laplacian_variance(to_gray(candidate))
        if abs(value - target) <= tolerance:
            return candidate
        if value > target:
            low = sigma
        else:
            high = sigma
    raise ValueError(f"no blur sigma up to {max_sigma} reaches Laplacian variance {target}")
