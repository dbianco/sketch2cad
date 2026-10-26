"""DXF ``TEXT`` of snapshot texts with the ``S2C_TEXT`` style (spec section 13.1, REQ-12).

A text's height is given in paper millimetres, so in model space (metres) it is
``height_mm_paper * paper_scale / 1000``: 2.5 mm at 1:100 is 0.25 m. The text is inserted
at ``anchor_m`` with the default left/baseline alignment, as the editor draws it, and
rotated counter-clockwise by ``rotation_deg``. Content is written as is; R2013 files are
UTF-8, so accents and any other Unicode character survive. ``S2C_TEXT`` is also the style
of block attributes (:mod:`sketch2cad.export.dxf_symbols`) and of the draft watermark.
"""

# ezdxf leaves ``dxfattribs`` parameters unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ezdxf.document import Drawing
from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.layouts.layout import Modelspace

if TYPE_CHECKING:
    from sketch2cad.export.dxf import ExportContext

TEXT_STYLE = "S2C_TEXT"
TEXT_FONT = "OpenSans-Regular.ttf"


def add_text_style(doc: Drawing) -> None:
    """Add the ``S2C_TEXT`` style (variable height, so each TEXT sets its own)."""
    doc.styles.add(TEXT_STYLE, font=TEXT_FONT)


def paper_height_m(height_mm_paper: float, paper_scale: int) -> float:
    """Model-space height in metres of a text ``height_mm_paper`` tall on paper."""
    return float(height_mm_paper) * paper_scale / 1000.0


def write_text(
    msp: Modelspace, entity: Mapping[str, Any], context: "ExportContext"
) -> list[DXFGraphic]:
    """One ``TEXT`` on the entity's layer."""
    geometry = entity["geometry"]
    x, y = geometry["anchor_m"]
    text = msp.add_text(
        str(geometry["content"]),
        height=paper_height_m(geometry["height_mm_paper"], context.paper_scale),
        rotation=float(geometry.get("rotation_deg", 0.0)),
        dxfattribs={"layer": entity["layer"], "style": TEXT_STYLE, "insert": (float(x), float(y))},
    )
    return [text]
