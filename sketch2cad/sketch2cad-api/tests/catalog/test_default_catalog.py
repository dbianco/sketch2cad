"""The ``default-1`` catalog: 10 layers, 8 neutral symbols, safe SVG, buildable blocks (H2-13)."""

# ezdxf leaves ``dxfattribs`` parameters unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false

import hashlib
import json
import re
from typing import Any

import pytest
from ezdxf.filemanagement import new
from pydantic import ValidationError

from sketch2cad.catalog import catalog_json, check_safe_svg, default_catalog
from sketch2cad.catalog.schema import (
    CatalogDefinition,
    DxfArc,
    DxfAttributeDefinition,
    DxfCircle,
    DxfLine,
    DxfPolyline,
    Layer,
    Symbol,
    UnsafeSvgError,
)

SPEC_LAYER_COLORS = {
    "L_STRUCTURE": 7,
    "L_ACCESS": 4,
    "L_EVIDENCE": 1,
    "L_SYMBOLS": 3,
    "L_DIMENSIONS": 2,
    "L_TEXT": 7,
    "L_ORIENTATION": 5,
    "L_ANNOTATIONS": 6,
    "L_CONTROL": 8,
    "L_SHEET": 7,
}
"""Spec section 12.1, in order."""
SPEC_SYMBOLS = {
    "north_arrow": ("paper_mm", "L_ORIENTATION"),
    "evidence_marker": ("paper_mm", "L_EVIDENCE"),
    "control_point": ("paper_mm", "L_CONTROL"),
    "door": ("world_m", "L_ACCESS"),
    "window": ("world_m", "L_ACCESS"),
    "generic_vehicle": ("world_m", "L_SYMBOLS"),
    "traffic_light": ("paper_mm", "L_SYMBOLS"),
    "tree": ("paper_mm", "L_SYMBOLS"),
}
"""Spec section 12.3: size mode and default layer of each symbol."""
EXCLUDED_TERMS = re.compile(r"victim|blood|weapon|casing|vict|arma|sangre|casquillo", re.I)
# default-1 is frozen once released; a new catalog version gets a new module and digest.
DEFAULT_1_SHA256 = "1d52ca98141f8314a96a9458e8d851ea666411d4f9d0e5cb68b8573318b6b29c"
SAFE_SVG = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><circle r='4'/></svg>"


def svg_with(body: str, root_attributes: str = "") -> str:
    return f"<svg xmlns='http://www.w3.org/2000/svg' {root_attributes}>{body}</svg>"


def symbol_data(**changes: Any) -> dict[str, Any]:
    data = catalog_json(default_catalog())["symbols"][1]
    return {**data, **changes}


def test_default_catalog_has_the_10_layers_and_8_symbols_of_the_spec() -> None:
    catalog = default_catalog()

    assert catalog.version == "default-1"
    assert {layer.name: layer.aci_color for layer in catalog.layers} == SPEC_LAYER_COLORS
    assert [layer.name for layer in catalog.layers] == list(SPEC_LAYER_COLORS)
    assert len(catalog.symbols) == 8
    assert {
        symbol.symbol_id: (symbol.size_mode, symbol.default_layer) for symbol in catalog.symbols
    } == SPEC_SYMBOLS


def test_no_symbol_names_an_excluded_subject() -> None:
    for symbol in default_catalog().symbols:
        assert not EXCLUDED_TERMS.search(symbol.symbol_id)
        assert not EXCLUDED_TERMS.search(symbol.label)


def test_every_symbol_has_svg_block_entities_size_mode_and_size() -> None:
    for symbol in default_catalog().symbols:
        assert symbol.svg.strip()
        assert check_safe_svg(symbol.svg) == symbol.svg
        assert symbol.dxf_block.name == f"S2C_{symbol.symbol_id}"
        assert symbol.dxf_block.entities
        assert symbol.size_mode in {"paper_mm", "world_m"}
        assert symbol.default_size > 0


def test_spec_sizes_and_attributes() -> None:
    symbols = {symbol.symbol_id: symbol for symbol in default_catalog().symbols}
    marker = symbols["evidence_marker"]
    vehicle = symbols["generic_vehicle"]

    assert [(a.name, a.type, a.required) for a in marker.attributes] == [
        ("evidence_number", "integer", True),
        ("label", "string", False),
    ]
    assert [a.name for a in vehicle.attributes] == ["label"]
    assert vehicle.default_size == 4.5
    polyline = vehicle.dxf_block.entities[0]
    assert isinstance(polyline, DxfPolyline)
    widths = [y for _, y in polyline.points]
    assert (max(widths) - min(widths)) * vehicle.default_size == pytest.approx(1.8)


def test_default_1_content_is_frozen() -> None:
    canonical = json.dumps(
        catalog_json(default_catalog()), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == DEFAULT_1_SHA256


def test_every_block_builds_and_inserts_in_ezdxf() -> None:
    doc = new("R2013")
    for layer in default_catalog().layers:
        doc.layers.add(layer.name, color=layer.aci_color)
    modelspace = doc.modelspace()
    for symbol in default_catalog().symbols:
        block = doc.blocks.new(symbol.dxf_block.name, base_point=symbol.dxf_block.base_point)
        for entity in symbol.dxf_block.entities:
            match entity:
                case DxfLine():
                    block.add_line(entity.start, entity.end)
                case DxfCircle():
                    block.add_circle(entity.center, entity.radius)
                case DxfArc():
                    block.add_arc(
                        entity.center, entity.radius, entity.start_angle, entity.end_angle
                    )
                case DxfPolyline():
                    block.add_lwpolyline(entity.points, close=entity.closed)
                case DxfAttributeDefinition():
                    block.add_attdef(
                        entity.tag, entity.insert, dxfattribs={"height": entity.height}
                    )
        values = {attribute.name.upper(): "1" for attribute in symbol.attributes}
        modelspace.add_blockref(
            symbol.dxf_block.name, (0, 0), dxfattribs={"layer": symbol.default_layer}
        ).add_auto_attribs(values)

    auditor = doc.audit()
    assert auditor.has_errors is False
    assert len(modelspace.query("INSERT")) == 8


@pytest.mark.parametrize(
    "svg",
    [
        svg_with("<script>alert(1)</script>"),
        svg_with("<circle r='4' onload='alert(1)'/>"),
        svg_with("<g onclick='steal()'/>"),
        svg_with("", "onmouseover='x()'"),
        svg_with("<image href='https://example.com/x.png'/>"),
        svg_with("<use href='https://example.com/sprite.svg#a'/>"),
        svg_with(
            "<path d='M0,0' xlink:href='http://example.com/a'/>",
            "xmlns:xlink='http://www.w3.org/1999/xlink'",
        ),
        svg_with("<a href='javascript:alert(1)'><circle r='1'/></a>"),
        svg_with("<path d='M0,0' fill='url(https://example.com/p.svg#g)'/>"),
        svg_with("<circle r='4' style='fill:red'/>"),
        svg_with("<style>@import 'https://example.com/a.css';</style>"),
        svg_with("<foreignObject><div/></foreignObject>"),
        "<!DOCTYPE svg [<!ENTITY x SYSTEM 'file:///etc/passwd'>]>" + svg_with("&x;"),
        "<?xml-stylesheet href='https://example.com/a.css'?>" + svg_with(""),
        svg_with("<text>hello</text>"),
        "<html><svg/></html>",
        "<svg viewBox='0 0 1 1'><circle r='1'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><circle",
    ],
)
def test_unsafe_or_malformed_svg_is_rejected(svg: str) -> None:
    with pytest.raises(UnsafeSvgError):
        check_safe_svg(svg)
    with pytest.raises(ValidationError):
        Symbol.model_validate(symbol_data(svg=svg))


def test_safe_static_svg_is_accepted() -> None:
    assert check_safe_svg(SAFE_SVG) == SAFE_SVG
    assert Symbol.model_validate(symbol_data(svg=SAFE_SVG)).svg == SAFE_SVG


@pytest.mark.parametrize(
    "changes",
    [
        {"svg": ""},
        {"size_mode": "pixels"},
        {"default_size": 0},
        {"symbol_id": "Bad Id"},
        {"dxf_block": {"name": "S2C_evidence_marker", "entities": []}},
        {
            "dxf_block": {
                "name": "S2C_other",
                "entities": [{"type": "LINE", "start": [0, 0], "end": [1, 1]}],
            }
        },
        {"attributes": [{"name": "evidence_number", "type": "integer", "required": True}]},
        {"extra": True},
    ],
)
def test_invalid_symbol_definitions_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Symbol.model_validate(symbol_data(**changes))


def test_catalog_rejects_duplicates_and_unknown_layers() -> None:
    catalog = default_catalog()
    layers = list(catalog.layers)
    symbols = list(catalog.symbols)

    with pytest.raises(ValidationError, match="layer names must be unique"):
        CatalogDefinition(version="x", layers=[*layers, layers[0]], symbols=symbols)
    with pytest.raises(ValidationError, match="symbol ids must be unique"):
        CatalogDefinition(version="x", layers=layers, symbols=[*symbols, symbols[0]])
    with pytest.raises(ValidationError, match="north_arrow: unknown layer L_ORIENTATION"):
        CatalogDefinition(
            version="x", layers=[Layer(name="L_X", description="x", aci_color=1)], symbols=symbols
        )
