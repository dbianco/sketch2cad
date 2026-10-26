"""DXF layer table built from the catalog only (spec sections 12.1 and 13.1).

Every layer comes from :class:`~sketch2cad.catalog.CatalogDefinition` with its ACI color;
nothing is created ad hoc. ezdxf always creates ``0`` and ``Defpoints``, so those two are
the only extra layers a document may hold. Catalog layers carry no linetype, so they keep
the ezdxf default (``Continuous``).
"""

# ezdxf leaves ``dxfattribs`` parameters unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false

from ezdxf.document import Drawing

from sketch2cad.catalog import CatalogDefinition

BUILTIN_LAYERS = frozenset({"0", "Defpoints"})


def catalog_layer_names(catalog: CatalogDefinition) -> frozenset[str]:
    """Names of the layers ``catalog`` defines."""
    return frozenset(layer.name for layer in catalog.layers)


def allowed_layer_names(catalog: CatalogDefinition) -> frozenset[str]:
    """Layers an exported document may contain: the catalog's plus ``0`` and ``Defpoints``."""
    return catalog_layer_names(catalog) | BUILTIN_LAYERS


def add_catalog_layers(doc: Drawing, catalog: CatalogDefinition) -> None:
    """Add every catalog layer to ``doc`` with its ACI color, in catalog order."""
    for layer in catalog.layers:
        doc.layers.add(layer.name, color=layer.aci_color)
