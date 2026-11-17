"""Scale proposal of the technical PDF (spec section 13.2, REQ-13).

The plan is drawn at one of the standard scales :data:`STANDARD_SCALES`, never fitted to the
page. The proposal is the largest scale (smallest denominator) at which the plan extents fit
in the drawing area of the sheet, which already keeps the 15 mm margin and leaves the
title-block column free (:func:`~sketch2cad.export.pdf_sheet.sheet_layout`). When the plan
does not fit even at 1:500 the proposal is 1:500 with ``fits=False``: the caller decides
whether to warn, change the sheet or accept a clipped drawing.
"""

from dataclasses import dataclass

from sketch2cad.export.pdf_sheet import A3, A4, Rect, SheetSpec, sheet_layout

STANDARD_SCALES: tuple[int, ...] = (50, 100, 200, 250, 500)
FIT_TOLERANCE_MM = 1e-9


@dataclass(frozen=True)
class Bounds:
    """Extents of the plan in local-frame metres."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)


@dataclass(frozen=True)
class ScaleProposal:
    """``scale`` is the denominator (100 for 1:100); ``fits`` is False only when even the
    smallest standard scale overflows the drawing area."""

    scale: int
    fits: bool


def fits_at(bounds: Bounds | None, area: Rect, scale: int) -> bool:
    """Whether the plan extents, at ``1:scale``, fit in ``area`` (paper millimetres)."""
    if bounds is None:
        return True
    width_mm = bounds.width * 1000.0 / scale
    height_mm = bounds.height * 1000.0 / scale
    return width_mm <= area.width + FIT_TOLERANCE_MM and height_mm <= area.height + FIT_TOLERANCE_MM


def propose_scale(bounds: Bounds | None, sheet: SheetSpec) -> ScaleProposal:
    """Largest standard scale at which ``bounds`` fits in the drawing area of ``sheet``.

    An empty plan (``bounds`` ``None``) gets the largest scale, 1:50.
    """
    area = sheet_layout(sheet).drawing_area
    for scale in STANDARD_SCALES:
        if fits_at(bounds, area, scale):
            return ScaleProposal(scale, True)
    return ScaleProposal(STANDARD_SCALES[-1], False)


def propose_sheet(bounds: Bounds | None) -> tuple[SheetSpec, ScaleProposal]:
    """Sheet and scale for ``sheet="auto"``: the sheet giving the larger scale, A4 on a tie.

    When the plan fits on neither sheet the answer is A3 at 1:500 with ``fits=False``.
    """
    a4, a3 = propose_scale(bounds, A4), propose_scale(bounds, A3)
    if a4.fits and a4.scale <= a3.scale:
        return A4, a4
    return A3, a3
