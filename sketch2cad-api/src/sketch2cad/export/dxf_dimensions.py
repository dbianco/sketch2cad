"""DXF aligned ``DIMENSION`` of snapshot dimensions (spec sections 8.2, 13.1; REQ-12).

A ``dimension`` becomes an aligned ``DIMENSION`` (``dimtype`` 1) from ``p1_m`` to
``p2_m`` whose dimension line lies ``offset_m`` to the left of ``p1_m -> p2_m`` (a negative
offset puts it on the right). Its definition points keep the real geometry, so a CAD
program measures the real length; only the displayed text is overridden:

- with ``declared_value_m`` the text is the declared value, two decimals and a point
  (``4.20``), even when the geometry measures 4.24 m;
- without it the text is the measured length, formatted the same way.

The discrepancy flag is not stored in the snapshot, so it is recomputed here with the rule
of :func:`sketch2cad.entities.dimensions.dimension_discrepancy` and the tolerance of
:attr:`ExportContext.dimension_tolerances`. A discrepant dimension uses the alert style
:data:`DIM_ALERT_STYLE` (red lines and text) instead of :data:`DIM_STYLE`; both are added by
:func:`add_dimension_styles`, sized in paper millimetres like texts and using ``S2C_TEXT``.

``render()`` draws each dimension into an anonymous block ``*D<n>``; ``n`` follows the
creation order of the dimensions, so two exports of the same snapshot name them alike.
"""

# ezdxf leaves ``dxfattribs`` parameters unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ezdxf.document import Drawing
from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.layouts.layout import Modelspace

from sketch2cad.entities.dimensions import dimension_discrepancy, measured_length
from sketch2cad.export.dxf_text import TEXT_STYLE, paper_height_m
from sketch2cad.geometry.metrics import TANGENCY_TOLERANCE_M

if TYPE_CHECKING:
    from sketch2cad.export.dxf import ExportContext

DIM_STYLE = "S2C_DIM"
DIM_ALERT_STYLE = "S2C_DIM_ALERT"
DIM_ALIGNED = 1
DIMTYPE_KIND_MASK = 0b111
"""Low bits of ``dimtype`` holding the dimension kind; higher bits are flags."""
BYLAYER = 256
ALERT_COLOR = 1  # ACI red
TEXT_HEIGHT_MM_PAPER = 2.5
ARROW_SIZE_MM_PAPER = 2.5
EXTENSION_BEYOND_MM_PAPER = 1.25
EXTENSION_OFFSET_MM_PAPER = 0.625
TEXT_GAP_MM_PAPER = 0.625
ARROW_BLOCK = "ARCHTICK"
DECIMAL_UNITS = 2
DECIMALS = 2
MIN_LENGTH_M = TANGENCY_TOLERANCE_M


class DegenerateDimensionError(ValueError):
    """A dimension whose two points coincide has no direction to be drawn along."""


def add_dimension_styles(doc: Drawing, paper_scale: int) -> None:
    """Add ``S2C_DIM`` and its red twin ``S2C_DIM_ALERT``, sized for ``paper_scale``.

    Decimal units with two decimals, ``.`` as separator and trailing zeros kept, so a
    measurement CAD recomputes reads like the exported text (``4.20``).
    """
    common: dict[str, Any] = {
        "dimtxsty": TEXT_STYLE,
        "dimtxt": paper_height_m(TEXT_HEIGHT_MM_PAPER, paper_scale),
        "dimasz": paper_height_m(ARROW_SIZE_MM_PAPER, paper_scale),
        "dimexe": paper_height_m(EXTENSION_BEYOND_MM_PAPER, paper_scale),
        "dimexo": paper_height_m(EXTENSION_OFFSET_MM_PAPER, paper_scale),
        "dimgap": paper_height_m(TEXT_GAP_MM_PAPER, paper_scale),
        "dimblk": ARROW_BLOCK,
        "dimtad": 1,
        "dimlunit": DECIMAL_UNITS,
        "dimdec": DECIMALS,
        "dimdsep": ord("."),
        "dimzin": 0,
        "dimrnd": 0.0,
        "dimlfac": 1.0,
        "dimpost": "",
    }
    for name, color in ((DIM_STYLE, BYLAYER), (DIM_ALERT_STYLE, ALERT_COLOR)):
        colors = {"dimclrd": color, "dimclre": color, "dimclrt": color}
        doc.dimstyles.add(name, dxfattribs={**common, **colors})


def dimension_text(value_m: float) -> str:
    """``value_m`` with two decimals and a point: ``4.2`` -> ``4.20``."""
    return f"{value_m:.{DECIMALS}f}"


def write_dimension(
    msp: Modelspace, entity: Mapping[str, Any], context: "ExportContext"
) -> list[DXFGraphic]:
    """One rendered aligned ``DIMENSION`` on the entity's layer.

    Raises :class:`DegenerateDimensionError` when ``p1_m`` and ``p2_m`` coincide.
    """
    geometry = entity["geometry"]
    p1 = (float(geometry["p1_m"][0]), float(geometry["p1_m"][1]))
    p2 = (float(geometry["p2_m"][0]), float(geometry["p2_m"][1]))
    measured = measured_length(p1, p2)
    if measured < MIN_LENGTH_M:
        raise DegenerateDimensionError("dimension points p1_m and p2_m coincide")
    declared = geometry.get("declared_value_m")
    text = dimension_text(float(declared) if declared is not None else measured)
    discrepant = dimension_discrepancy(entity["type"], geometry, context.dimension_tolerances)
    override = msp.add_aligned_dim(
        p1=p1,
        p2=p2,
        distance=float(geometry.get("offset_m", 0.0)),
        text=text,
        dimstyle=DIM_ALERT_STYLE if discrepant else DIM_STYLE,
        dxfattribs={"layer": entity["layer"]},
    )
    dimension = override.dimension
    # ezdxf writes a rotated linear dimension (kind 0); mark it aligned, keeping the flags.
    dimension.dxf.dimtype = (dimension.dxf.dimtype & ~DIMTYPE_KIND_MASK) | DIM_ALIGNED
    override.render()
    return [dimension]
