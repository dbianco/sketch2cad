"""DXF blocks and ``INSERT``s of catalog symbols and evidence markers (spec 12.2, 13.1).

Each catalog symbol a snapshot uses becomes the block ``S2C_<symbol_id>`` exactly once,
built from its ``dxf_block`` primitives on layer ``0`` (so they take the layer of the
``INSERT``). Blocks are drawn at unit size, so the ``INSERT`` scale carries the size:

- ``paper_mm`` (annotative): ``default_size * scale * paper_scale / 1000`` metres, so a
  6 mm marker is 0.6 m at 1:100 and 0.3 m at 1:50;
- ``world_m``: ``default_size * scale`` metres (a 0.9 m door at scale 1 is 0.9).

Every ``ATTDEF`` gets an ``ATTRIB`` on the ``INSERT`` whose value is the symbol attribute
of the same name (``evidence_number`` fills tag ``EVIDENCE_NUMBER``) written as text
(``3``, not ``3.0``); a missing attribute gives an empty ``ATTRIB``.

A ``point`` that places evidence (``evidence_id`` set) is also drawn as an
``evidence_marker`` ``INSERT`` at the point, at the catalog size. Its number and label come
from the snapshot's ``evidence_table`` row of the entity (spec section 8.3) or, while that
table is still empty (filled by tasks 089/091), from the entity's own ``attributes``.
"""

# ezdxf leaves ``dxfattribs`` parameters unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ezdxf.document import Drawing
from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.layouts.layout import Modelspace

from sketch2cad.catalog import CatalogDefinition, Symbol
from sketch2cad.catalog.schema import (
    DxfArc,
    DxfCircle,
    DxfLine,
    DxfPolyline,
)
from sketch2cad.export.dxf_text import TEXT_STYLE

if TYPE_CHECKING:
    from sketch2cad.export.dxf import ExportContext

EVIDENCE_MARKER = "evidence_marker"
EVIDENCE_ATTRIBUTES = ("evidence_number", "label")


class UnknownSymbolError(ValueError):
    """The snapshot names a symbol its catalog does not define."""


def find_symbol(catalog: CatalogDefinition, symbol_id: str) -> Symbol:
    """The catalog symbol ``symbol_id``; raise :class:`UnknownSymbolError` if absent."""
    for symbol in catalog.symbols:
        if symbol.symbol_id == symbol_id:
            return symbol
    raise UnknownSymbolError(f"symbol {symbol_id!r} is not in catalog {catalog.version!r}")


def insert_scale(symbol: Symbol, scale: float, paper_scale: int) -> float:
    """``INSERT`` scale in metres per block unit of ``symbol`` drawn at entity ``scale``."""
    size = symbol.default_size * scale
    return size * paper_scale / 1000.0 if symbol.size_mode == "paper_mm" else size


def attribute_text(value: object) -> str:
    """An attribute value as ATTRIB text: ``3`` and ``3.0`` give ``3``, ``None`` gives ``""``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def ensure_block(doc: Drawing, symbol: Symbol) -> str:
    """Name of the block of ``symbol``, defining it on first use."""
    spec = symbol.dxf_block
    if spec.name in doc.blocks:
        return spec.name
    block = doc.blocks.new(spec.name, base_point=spec.base_point)
    for item in spec.entities:
        if isinstance(item, DxfLine):
            block.add_line(item.start, item.end)
        elif isinstance(item, DxfCircle):
            block.add_circle(item.center, item.radius)
        elif isinstance(item, DxfArc):
            block.add_arc(item.center, item.radius, item.start_angle, item.end_angle)
        elif isinstance(item, DxfPolyline):
            block.add_lwpolyline(item.points, format="xy", close=item.closed)
        else:  # DxfAttributeDefinition, the last member of the DxfEntity union
            block.add_attdef(
                item.tag,
                item.insert,
                text="",
                height=item.height,
                dxfattribs={"style": TEXT_STYLE},
            )
    return spec.name


def add_symbol_insert(
    msp: Modelspace,
    symbol: Symbol,
    *,
    layer: str,
    anchor: tuple[float, float],
    rotation_deg: float,
    scale: float,
    paper_scale: int,
    attributes: Mapping[str, Any],
) -> DXFGraphic:
    """``INSERT`` of ``symbol`` with one ``ATTRIB`` per ``ATTDEF``, all on ``layer``."""
    name = ensure_block(msp.doc, symbol)
    factor = insert_scale(symbol, scale, paper_scale)
    insert = msp.add_blockref(
        name,
        anchor,
        dxfattribs={
            "layer": layer,
            "xscale": factor,
            "yscale": factor,
            "zscale": factor,
            "rotation": rotation_deg,
        },
    )
    values = {
        attribute.name.upper(): attribute_text(attributes.get(attribute.name))
        for attribute in symbol.attributes
    }
    if values:
        insert.add_auto_attribs(values)
        for attrib in insert.attribs:
            attrib.dxf.layer = layer
    return insert


def write_symbol(
    msp: Modelspace, entity: Mapping[str, Any], context: "ExportContext"
) -> list[DXFGraphic]:
    """``INSERT`` of block ``S2C_<symbol_id>`` at the entity anchor."""
    geometry = entity["geometry"]
    symbol = find_symbol(context.catalog, geometry["symbol_id"])
    x, y = geometry["anchor_m"]
    insert = add_symbol_insert(
        msp,
        symbol,
        layer=entity["layer"],
        anchor=(float(x), float(y)),
        rotation_deg=float(geometry.get("rotation_deg", 0.0)),
        scale=float(geometry.get("scale", 1.0)),
        paper_scale=context.paper_scale,
        attributes=geometry.get("attributes") or {},
    )
    return [insert]


def evidence_marker_attributes(
    entity: Mapping[str, Any], evidence_table: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """``evidence_number`` and ``label`` of an evidence point, table first."""
    for row in evidence_table:
        if row.get("entity_id") == entity["id"]:
            return {name: row.get(name) for name in EVIDENCE_ATTRIBUTES}
    own: Mapping[str, Any] = entity.get("attributes") or {}
    return {name: own.get(name) for name in EVIDENCE_ATTRIBUTES}


def write_evidence_marker(
    msp: Modelspace, entity: Mapping[str, Any], context: "ExportContext"
) -> list[DXFGraphic]:
    """``evidence_marker`` ``INSERT`` at an evidence point, or nothing for a plain point."""
    if entity.get("evidence_id") is None:
        return []
    symbol = find_symbol(context.catalog, EVIDENCE_MARKER)
    ((x, y),) = entity["geometry"]["coords_m"]
    insert = add_symbol_insert(
        msp,
        symbol,
        layer=entity["layer"],
        anchor=(float(x), float(y)),
        rotation_deg=0.0,
        scale=1.0,
        paper_scale=context.paper_scale,
        attributes=evidence_marker_attributes(entity, context.evidence_table),
    )
    return [insert]
