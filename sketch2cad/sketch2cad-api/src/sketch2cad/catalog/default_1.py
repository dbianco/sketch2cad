"""Catalog version ``default-1``: the ten layers of spec section 12.1.

This version is seeded by migration ``0006_seed_catalog`` and referenced by exported
manifests (``catalog_version``), so its content is frozen once released: changes go to a
new version module, never here. ``tests/catalog/test_default_catalog.py`` pins a digest.
"""

from sketch2cad.catalog.schema import Layer

VERSION = "default-1"

LAYERS: tuple[Layer, ...] = (
    Layer(name="L_STRUCTURE", description="Muros, bordes de calzada, límites", aci_color=7),
    Layer(name="L_ACCESS", description="Puertas, ventanas, accesos", aci_color=4),
    Layer(
        name="L_EVIDENCE",
        description="Indicios y objetos documentados (puntos y símbolos de indicio)",
        aci_color=1,
    ),
    Layer(
        name="L_SYMBOLS",
        description="Símbolos no indiciarios (vehículo genérico, mobiliario, semáforo)",
        aci_color=3,
    ),
    Layer(name="L_DIMENSIONS", description="Cotas", aci_color=2),
    Layer(name="L_TEXT", description="Etiquetas y notas", aci_color=7),
    Layer(name="L_ORIENTATION", description="Norte, ejes locales", aci_color=5),
    Layer(name="L_ANNOTATIONS", description="Observaciones del técnico o revisor", aci_color=6),
    Layer(name="L_CONTROL", description="Puntos de control y calibración", aci_color=8),
    Layer(
        name="L_SHEET",
        description="Cajetín, marco, leyenda, marca de agua (solo en exportación)",
        aci_color=7,
    ),
)
