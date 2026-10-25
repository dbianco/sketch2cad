"""Catalog of layers and symbols (spec section 12); ``default-1`` is the MVP seed."""

from functools import cache
from typing import Any

from sketch2cad.catalog import default_1, symbols
from sketch2cad.catalog.schema import CatalogDefinition, Layer, Symbol, check_safe_svg

__all__ = [
    "CatalogDefinition",
    "Layer",
    "Symbol",
    "catalog_json",
    "check_safe_svg",
    "default_catalog",
]


@cache
def default_catalog() -> CatalogDefinition:
    """The validated ``default-1`` catalog: ten layers and eight neutral symbols."""
    return CatalogDefinition(
        version=default_1.VERSION,
        layers=list(default_1.LAYERS),
        symbols=list(symbols.SYMBOLS),
    )


def catalog_json(catalog: CatalogDefinition) -> dict[str, Any]:
    """``version``, ``layers`` and ``symbols`` as JSON-ready values for the ``catalog`` row."""
    return catalog.model_dump(mode="json")
