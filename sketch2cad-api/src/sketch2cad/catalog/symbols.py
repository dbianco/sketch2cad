"""The eight symbols of catalog ``default-1`` (spec section 12.3).

Geometry follows the conventions of :mod:`sketch2cad.catalog.schema`: one block unit is
the symbol size, the base point (0, 0) is the entity anchor and SVG user units are block
units times 100 with Y flipped. Symbols that sit on a wall (``door``, ``window``) start at
the anchor and run along +X; the others are centered on it. ``generic_vehicle`` faces +X.
Per the author's decision in 12.3 the default catalog carries only neutral symbols;
institutions add their own symbology as a new catalog version.
"""

from sketch2cad.catalog.schema import (
    DxfArc,
    DxfAttributeDefinition,
    DxfBlock,
    DxfCircle,
    DxfLine,
    DxfPolyline,
    Symbol,
    SymbolAttribute,
)

_SVG_OPEN = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='{}'>"
_STROKE = "fill='none' stroke='currentColor' stroke-width='4'"


def _svg(view_box: str, *shapes: str) -> str:
    return _SVG_OPEN.format(view_box) + f"<g {_STROKE}>" + "".join(shapes) + "</g></svg>"


NORTH_ARROW = Symbol(
    symbol_id="north_arrow",
    label="Flecha de norte",
    default_layer="L_ORIENTATION",
    svg=_svg(
        "-50 -55 100 100",
        "<polygon points='0,-30 20,40 0,20 -20,40'/>",
        "<path d='M-7,-35 V-50 L7,-35 V-50'/>",
    ),
    dxf_block=DxfBlock(
        name="S2C_north_arrow",
        entities=[
            DxfPolyline(points=[(0, 0.3), (0.2, -0.4), (0, -0.2), (-0.2, -0.4)], closed=True),
            DxfLine(start=(-0.07, 0.35), end=(-0.07, 0.5)),
            DxfLine(start=(-0.07, 0.5), end=(0.07, 0.35)),
            DxfLine(start=(0.07, 0.35), end=(0.07, 0.5)),
        ],
    ),
    size_mode="paper_mm",
    default_size=15,
)

EVIDENCE_MARKER = Symbol(
    symbol_id="evidence_marker",
    label="Marcador de indicio",
    default_layer="L_EVIDENCE",
    svg=_svg("-50 -50 100 100", "<circle cx='0' cy='0' r='46'/>"),
    dxf_block=DxfBlock(
        name="S2C_evidence_marker",
        entities=[
            DxfCircle(center=(0, 0), radius=0.5),
            DxfAttributeDefinition(tag="EVIDENCE_NUMBER", insert=(-0.2, -0.2), height=0.4),
            DxfAttributeDefinition(tag="LABEL", insert=(0.6, -0.15), height=0.3),
        ],
    ),
    size_mode="paper_mm",
    default_size=6,
    attributes=[
        SymbolAttribute(name="evidence_number", type="integer", required=True),
        SymbolAttribute(name="label", type="string"),
    ],
)

CONTROL_POINT = Symbol(
    symbol_id="control_point",
    label="Punto de control",
    default_layer="L_CONTROL",
    svg=_svg(
        "-50 -50 100 100",
        "<circle cx='0' cy='0' r='35'/>",
        "<line x1='-50' y1='0' x2='50' y2='0'/>",
        "<line x1='0' y1='-50' x2='0' y2='50'/>",
    ),
    dxf_block=DxfBlock(
        name="S2C_control_point",
        entities=[
            DxfCircle(center=(0, 0), radius=0.35),
            DxfLine(start=(-0.5, 0), end=(0.5, 0)),
            DxfLine(start=(0, -0.5), end=(0, 0.5)),
        ],
    ),
    size_mode="paper_mm",
    default_size=5,
)

DOOR = Symbol(
    symbol_id="door",
    label="Puerta",
    default_layer="L_ACCESS",
    svg=_svg(
        "-5 -105 110 110",
        "<line x1='0' y1='0' x2='0' y2='-100'/>",
        "<path d='M100,0 A100,100 0 0,0 0,-100'/>",
    ),
    dxf_block=DxfBlock(
        name="S2C_door",
        entities=[
            DxfLine(start=(0, 0), end=(0, 1)),
            DxfArc(center=(0, 0), radius=1, start_angle=0, end_angle=90),
        ],
    ),
    size_mode="world_m",
    default_size=0.9,
)

WINDOW = Symbol(
    symbol_id="window",
    label="Ventana",
    default_layer="L_ACCESS",
    svg=_svg(
        "-5 -10 110 20",
        "<rect x='0' y='-5' width='100' height='10'/>",
        "<line x1='0' y1='0' x2='100' y2='0'/>",
    ),
    dxf_block=DxfBlock(
        name="S2C_window",
        entities=[
            DxfPolyline(points=[(0, -0.05), (1, -0.05), (1, 0.05), (0, 0.05)], closed=True),
            DxfLine(start=(0, 0), end=(1, 0)),
        ],
    ),
    size_mode="world_m",
    default_size=1.2,
)

GENERIC_VEHICLE = Symbol(
    symbol_id="generic_vehicle",
    label="Vehículo genérico",
    default_layer="L_SYMBOLS",
    svg=_svg(
        "-55 -25 110 50",
        "<rect x='-50' y='-20' width='100' height='40'/>",
        "<line x1='20' y1='-18' x2='20' y2='18'/>",
        "<line x1='-30' y1='-18' x2='-30' y2='18'/>",
    ),
    dxf_block=DxfBlock(
        name="S2C_generic_vehicle",
        entities=[
            DxfPolyline(points=[(-0.5, -0.2), (0.5, -0.2), (0.5, 0.2), (-0.5, 0.2)], closed=True),
            DxfLine(start=(0.2, -0.18), end=(0.2, 0.18)),
            DxfLine(start=(-0.3, -0.18), end=(-0.3, 0.18)),
            DxfAttributeDefinition(tag="LABEL", insert=(-0.25, -0.05), height=0.1),
        ],
    ),
    size_mode="world_m",
    default_size=4.5,
    attributes=[SymbolAttribute(name="label", type="string")],
)

TRAFFIC_LIGHT = Symbol(
    symbol_id="traffic_light",
    label="Semáforo",
    default_layer="L_SYMBOLS",
    svg=_svg(
        "-50 -50 100 100",
        "<rect x='-20' y='-50' width='40' height='100'/>",
        "<circle cx='0' cy='-30' r='12'/>",
        "<circle cx='0' cy='0' r='12'/>",
        "<circle cx='0' cy='30' r='12'/>",
    ),
    dxf_block=DxfBlock(
        name="S2C_traffic_light",
        entities=[
            DxfPolyline(points=[(-0.2, -0.5), (0.2, -0.5), (0.2, 0.5), (-0.2, 0.5)], closed=True),
            DxfCircle(center=(0, 0.3), radius=0.12),
            DxfCircle(center=(0, 0), radius=0.12),
            DxfCircle(center=(0, -0.3), radius=0.12),
        ],
    ),
    size_mode="paper_mm",
    default_size=8,
)

TREE = Symbol(
    symbol_id="tree",
    label="Árbol",
    default_layer="L_SYMBOLS",
    svg=_svg(
        "-50 -50 100 100",
        "<circle cx='0' cy='0' r='48'/>",
        "<circle cx='0' cy='0' r='8'/>",
    ),
    dxf_block=DxfBlock(
        name="S2C_tree",
        entities=[DxfCircle(center=(0, 0), radius=0.5), DxfCircle(center=(0, 0), radius=0.08)],
    ),
    size_mode="paper_mm",
    default_size=10,
)

SYMBOLS: tuple[Symbol, ...] = (
    NORTH_ARROW,
    EVIDENCE_MARKER,
    CONTROL_POINT,
    DOOR,
    WINDOW,
    GENERIC_VEHICLE,
    TRAFFIC_LIGHT,
    TREE,
)
