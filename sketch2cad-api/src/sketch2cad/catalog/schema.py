"""Pydantic models of a catalog version: layers (spec 12.1) and symbols (spec 12.2).

Symbol geometry is normalized: one block unit equals the symbol's size. ``paper_mm``
symbols are inserted in DXF with scale ``size_mm * paper_scale / 1000`` and ``world_m``
symbols with scale ``size_m``, so a block drawn at unit size ends up in real meters.
The block's base point is the entity's ``anchor_m``; block coordinates have Y up.

SVG previews use 100 user units per block unit with the anchor at (0, 0) and Y down, as
usual in SVG; each ``viewBox`` just frames the drawing. They draw with ``currentColor``
so the client colors them by layer. They must be static markup: :func:`check_safe_svg`
accepts only a small allow list of shape elements and presentation attributes, so no
script, event handler, link, style sheet or external reference can get through.
"""

import re
from typing import Annotated, Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SVG_ELEMENTS = frozenset({"svg", "g", "path", "line", "circle", "rect", "polyline", "polygon"})
SVG_ATTRIBUTES = frozenset(
    {
        "viewBox",
        "d",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "width",
        "height",
        "points",
        "transform",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
    }
)
_FORBIDDEN_SVG_TEXT = re.compile(r"<!|<\?|url\(|javascript:", re.IGNORECASE)
LAYER_NAME = r"^L_[A-Z]+$"
SYMBOL_ID = r"^[a-z][a-z0-9_]*$"
ATTRIBUTE_NAME = r"^[a-z][a-z0-9_]*$"

Point = tuple[float, float]


class UnsafeSvgError(ValueError):
    """The SVG markup is not well formed or uses something outside the allow list."""


def check_safe_svg(svg: str) -> str:
    """Return ``svg`` unchanged if it is safe static markup, else raise :class:`UnsafeSvgError`.

    Rejects declarations, processing instructions and entities (``<!``, ``<?``), CSS
    ``url(...)`` and ``javascript:`` anywhere, any element outside the shape allow list
    (``script``, ``image``, ``use``, ``a``, ``foreignObject``, ``style``...), and any
    attribute outside the presentation allow list (``on*`` handlers, ``href``,
    ``xlink:href``, ``style``...). Text content is not allowed either.
    """
    if _FORBIDDEN_SVG_TEXT.search(svg):
        raise UnsafeSvgError("SVG contains a declaration, url() or javascript: reference")
    try:
        root = ElementTree.fromstring(svg)  # noqa: S314 - entities and DTDs rejected above
    except ElementTree.ParseError as error:
        raise UnsafeSvgError(f"SVG is not well-formed XML: {error}") from None
    if root.tag != f"{{{SVG_NAMESPACE}}}svg":
        raise UnsafeSvgError("SVG root must be an <svg> element in the SVG namespace")
    for element in root.iter():
        namespace, _, name = element.tag.rpartition("}")
        if namespace != f"{{{SVG_NAMESPACE}" or name not in SVG_ELEMENTS:
            raise UnsafeSvgError(f"SVG element not allowed: {element.tag}")
        for attribute in element.attrib:
            if attribute not in SVG_ATTRIBUTES:
                raise UnsafeSvgError(f"SVG attribute not allowed: {attribute}")
        if (element.text or "").strip() or (element.tail or "").strip():
            raise UnsafeSvgError("SVG text content is not allowed")
    return svg


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Layer(_Strict):
    """One CAD layer with its DXF color as an AutoCAD Color Index (1-255)."""

    name: Annotated[str, Field(pattern=LAYER_NAME)]
    description: str
    aci_color: Annotated[int, Field(ge=1, le=255)]


class DxfLine(_Strict):
    type: Literal["LINE"] = "LINE"
    start: Point
    end: Point


class DxfCircle(_Strict):
    type: Literal["CIRCLE"] = "CIRCLE"
    center: Point
    radius: Annotated[float, Field(gt=0)]


class DxfArc(_Strict):
    """Counter-clockwise arc from ``start_angle`` to ``end_angle``, in degrees."""

    type: Literal["ARC"] = "ARC"
    center: Point
    radius: Annotated[float, Field(gt=0)]
    start_angle: float
    end_angle: float


class DxfPolyline(_Strict):
    type: Literal["LWPOLYLINE"] = "LWPOLYLINE"
    points: Annotated[list[Point], Field(min_length=2)]
    closed: bool = False


class DxfAttributeDefinition(_Strict):
    """ATTDEF filled from the symbol attribute whose upper-cased name is ``tag``."""

    type: Literal["ATTDEF"] = "ATTDEF"
    tag: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
    insert: Point
    height: Annotated[float, Field(gt=0)]


DxfEntity = Annotated[
    DxfLine | DxfCircle | DxfArc | DxfPolyline | DxfAttributeDefinition,
    Field(discriminator="type"),
]


class DxfBlock(_Strict):
    """DXF block definition; inserted as ``INSERT`` of ``name`` at the entity anchor."""

    name: Annotated[str, Field(pattern=r"^S2C_[a-z][a-z0-9_]*$")]
    base_point: Point = (0.0, 0.0)
    entities: Annotated[list[DxfEntity], Field(min_length=1)]


class SymbolAttribute(_Strict):
    name: Annotated[str, Field(pattern=ATTRIBUTE_NAME)]
    type: Literal["integer", "number", "string"]
    required: bool = False


class Symbol(_Strict):
    """One symbol of spec 12.2; ``default_size`` is in mm (``paper_mm``) or m (``world_m``)."""

    symbol_id: Annotated[str, Field(pattern=SYMBOL_ID)]
    label: Annotated[str, Field(min_length=1)]
    default_layer: Annotated[str, Field(pattern=LAYER_NAME)]
    svg: Annotated[str, Field(min_length=1)]
    dxf_block: DxfBlock
    size_mode: Literal["paper_mm", "world_m"]
    default_size: Annotated[float, Field(gt=0)]
    attributes: list[SymbolAttribute] = []

    @field_validator("svg")
    @classmethod
    def _svg_is_safe(cls, value: str) -> str:
        return check_safe_svg(value)

    @model_validator(mode="after")
    def _block_matches_symbol(self) -> "Symbol":
        if self.dxf_block.name != f"S2C_{self.symbol_id}":
            raise ValueError(f"dxf_block.name must be S2C_{self.symbol_id}")
        tags = [e.tag for e in self.dxf_block.entities if isinstance(e, DxfAttributeDefinition)]
        names = [attribute.name.upper() for attribute in self.attributes]
        if sorted(tags) != sorted(names):
            raise ValueError("dxf_block needs exactly one ATTDEF per symbol attribute")
        return self


class CatalogDefinition(_Strict):
    """A catalog version: unique layers, unique symbols, each on a known layer."""

    version: Annotated[str, Field(min_length=1)]
    layers: Annotated[list[Layer], Field(min_length=1)]
    symbols: list[Symbol]

    @model_validator(mode="after")
    def _references_are_consistent(self) -> "CatalogDefinition":
        layer_names = [layer.name for layer in self.layers]
        if len(set(layer_names)) != len(layer_names):
            raise ValueError("layer names must be unique")
        symbol_ids = [symbol.symbol_id for symbol in self.symbols]
        if len(set(symbol_ids)) != len(symbol_ids):
            raise ValueError("symbol ids must be unique")
        for symbol in self.symbols:
            if symbol.default_layer not in layer_names:
                raise ValueError(f"{symbol.symbol_id}: unknown layer {symbol.default_layer}")
        return self
