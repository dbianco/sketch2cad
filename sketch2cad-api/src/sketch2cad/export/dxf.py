"""DXF export of a frozen revision snapshot (spec section 13.1, REQ-12).

:func:`export_dxf` is a pure function of the snapshot dict (``revisions.snapshot``), the
catalog it names and two flags; it never reads the database, so the signed snapshot alone
reproduces the file. Coordinates are already metres in the local frame with Y up, which is
the DXF model space (1:1, ``$INSUNITS = 6``), so they are written unchanged.

Structure:

- :func:`new_document` holds every header setting (R2013, units, measurement);
- :func:`build_document` adds the catalog layers, the entities in snapshot order (handles
  follow creation order), the metadata carrier and the draft watermark;
- :func:`finalize_document` runs ``doc.audit()`` and is the single place where save-time
  metadata is set: the reproducibility settings of ``docs/spikes/dxf-reproducibility.md``
  (:mod:`sketch2cad.export.dxf_repro`) derive dates from ``frozen_at`` and GUIDs from the
  ``plan_id`` and the ``snapshot_sha256``, so the same revision always gives the same bytes;
- :func:`write_bytes` writes to a text stream and encodes it (UTF-8, ``\\n`` line endings).

Each snapshot entity type maps to a writer in :data:`ENTITY_WRITERS`: texts are written by
:mod:`sketch2cad.export.dxf_text`, symbols (and the marker of evidence points) by
:mod:`sketch2cad.export.dxf_symbols` and dimensions by :mod:`sketch2cad.export.dxf_dimensions`.
Types listed in :data:`DEFERRED_TYPES` (none left) have no writer yet and are skipped;
adding their writer to the table and removing them from the set is all it takes to export
them. Every written entity carries XDATA
``entity_id=<uuid>`` under :data:`APP_ID` so the DXF can be traced back to the snapshot.
"""

# ezdxf leaves ``dxfattribs`` parameters unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false

import io
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ezdxf import bbox, units
from ezdxf.document import Drawing
from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.filemanagement import new as new_drawing
from ezdxf.layouts.layout import Modelspace

from sketch2cad.catalog import CatalogDefinition
from sketch2cad.entities.dimensions import DimensionTolerances
from sketch2cad.export.dxf_dimensions import (
    DegenerateDimensionError,
    add_dimension_styles,
    write_dimension,
)
from sketch2cad.export.dxf_layers import add_catalog_layers, catalog_layer_names
from sketch2cad.export.dxf_repro import pin_document, repro_settings
from sketch2cad.export.dxf_symbols import (
    UnknownSymbolError,
    write_evidence_marker,
    write_symbol,
)
from sketch2cad.export.dxf_text import TEXT_STYLE, add_text_style, paper_height_m, write_text
from sketch2cad.plans.local_frame import PROVISIONAL_PAPER_SCALE
from sketch2cad.revisions.snapshot import snapshot_sha256

logger = logging.getLogger(__name__)

DXF_VERSION = "R2013"  # AC1027
INSUNITS_METERS = 6
MEASUREMENT_METRIC = 1
APP_ID = "SKETCH2CAD"
SHEET_LAYER = "L_SHEET"
DRAFT_WATERMARK = "DRAFT — NOT VALIDATED"
WATERMARK_HEIGHT_MM_PAPER = 10.0

Coordinate = tuple[float, float]
XData = list[tuple[int, Any]]


class DxfExportError(ValueError):
    """The snapshot cannot be exported (unknown layer, type or symbol) or the audit failed."""


@dataclass(frozen=True)
class ExportContext:
    """What entity writers may need besides the entity itself.

    ``dimension_tolerances`` decides which dimensions are drawn with the alert style; the
    snapshot does not store the discrepancy flag, so the export recomputes it.
    """

    catalog: CatalogDefinition
    paper_scale: int
    draft: bool
    evidence_table: Sequence[Mapping[str, Any]] = field(default_factory=tuple[Any, ...])
    dimension_tolerances: DimensionTolerances = field(default_factory=DimensionTolerances)


EntityWriter = Callable[[Modelspace, Mapping[str, Any], ExportContext], list[DXFGraphic]]


def _coords(geometry: Mapping[str, Any]) -> list[Coordinate]:
    return [(float(x), float(y)) for x, y in geometry["coords_m"]]


def _write_line(
    msp: Modelspace, entity: Mapping[str, Any], context: ExportContext
) -> list[DXFGraphic]:
    start, end = _coords(entity["geometry"])
    return [msp.add_line(start, end, dxfattribs={"layer": entity["layer"]})]


def _write_polyline(
    msp: Modelspace, entity: Mapping[str, Any], context: ExportContext
) -> list[DXFGraphic]:
    geometry = entity["geometry"]
    polyline = msp.add_lwpolyline(
        _coords(geometry),
        format="xy",
        close=bool(geometry.get("closed", False)),
        dxfattribs={"layer": entity["layer"]},
    )
    return [polyline]


def _write_point(
    msp: Modelspace, entity: Mapping[str, Any], context: ExportContext
) -> list[DXFGraphic]:
    """``POINT``, plus the evidence marker ``INSERT`` when the point places evidence."""
    (location,) = _coords(entity["geometry"])
    point = msp.add_point(location, dxfattribs={"layer": entity["layer"]})
    return [point, *write_evidence_marker(msp, entity, context)]


ENTITY_WRITERS: dict[str, EntityWriter] = {
    "line": _write_line,
    "polyline": _write_polyline,
    "point": _write_point,
    "text": write_text,
    "symbol": write_symbol,
    "dimension": write_dimension,
}
DEFERRED_TYPES: frozenset[str] = frozenset()


def paper_scale(snapshot: Mapping[str, Any]) -> int:
    """Scale denominator of the snapshot's calibration, or the provisional one (1:100)."""
    calibration = snapshot.get("calibration")
    value = None if calibration is None else calibration.get("paper_scale")
    return int(value) if value else PROVISIONAL_PAPER_SCALE


def new_document() -> Drawing:
    """Empty R2013 document in metres: the only place header variables are set."""
    doc = new_drawing(DXF_VERSION, setup=True, units=units.M)
    doc.header["$INSUNITS"] = INSUNITS_METERS
    doc.header["$MEASUREMENT"] = MEASUREMENT_METRIC
    doc.appids.add(APP_ID)
    add_text_style(doc)
    return doc


def metadata_xdata(snapshot: Mapping[str, Any], sha256: str, generator_version: str) -> XData:
    """Document metadata of spec section 13.1 as ``key=value`` XDATA strings.

    The revision number is repeated as a 32-bit integer (group code 1071).
    """
    pairs = (
        ("plan_id", snapshot["plan_id"]),
        ("revision_number", snapshot["revision_number"]),
        ("snapshot_sha256", sha256),
        ("metric_status", snapshot["metric_status"]),
        ("generator_version", generator_version),
    )
    xdata: XData = [(1000, f"{key}={value}") for key, value in pairs]
    xdata.append((1071, int(snapshot["revision_number"])))
    return xdata


def build_document(
    snapshot: dict[str, Any],
    catalog: CatalogDefinition,
    *,
    draft: bool,
    generator_version: str,
    frozen_at: datetime | None,
    dimension_tolerances: DimensionTolerances | None = None,
) -> Drawing:
    """The ezdxf document of ``snapshot``, audited and ready to write.

    ``frozen_at`` is the timezone-aware freeze time of the revision (``revisions.frozen_at``,
    it is not part of the snapshot); it dates the file. Pass ``None`` for a revision that is
    not frozen: the file is dated :data:`~sketch2cad.export.dxf_repro.DRAFT_EPOCH`.

    ``dimension_tolerances`` defaults to the spec tolerance (0.02 m); callers pass the
    configured one (``DimensionTolerances.from_settings``) so the alert style of discrepant
    dimensions matches the editor.

    Raises :class:`DxfExportError` when the snapshot names another catalog version, uses a
    layer or entity type the export does not know, has a dimension whose points coincide,
    or the audit reports errors or fixes, and :class:`ValueError` for a naive ``frozen_at``.
    """
    if snapshot["catalog_version"] != catalog.version:
        raise DxfExportError(
            f"snapshot uses catalog {snapshot['catalog_version']!r}, got {catalog.version!r}"
        )
    layers = catalog_layer_names(catalog)
    if SHEET_LAYER not in layers:
        raise DxfExportError(f"catalog {catalog.version!r} has no {SHEET_LAYER} layer")
    sha256 = snapshot_sha256(snapshot)
    context = ExportContext(
        catalog,
        paper_scale(snapshot),
        draft,
        tuple(snapshot.get("evidence_table") or ()),
        dimension_tolerances or DimensionTolerances(),
    )
    doc = new_document()
    add_dimension_styles(doc, context.paper_scale)
    add_catalog_layers(doc, catalog)
    msp = doc.modelspace()
    written = _add_entities(msp, snapshot["entities"], context, layers)
    carrier = msp.add_point((0.0, 0.0), dxfattribs={"layer": SHEET_LAYER, "invisible": 1})
    carrier.set_xdata(APP_ID, metadata_xdata(snapshot, sha256, generator_version))
    if draft:
        _add_watermark(msp, written, context)
    finalize_document(doc, plan_id=str(snapshot["plan_id"]), sha256=sha256, frozen_at=frozen_at)
    logger.info(
        "Built DXF of plan %s revision %s (%d entities, draft=%s)",
        snapshot["plan_id"],
        snapshot["revision_number"],
        len(written),
        draft,
    )
    return doc


def finalize_document(
    doc: Drawing, *, plan_id: str, sha256: str, frozen_at: datetime | None
) -> None:
    """Audit ``doc`` and pin its save-time metadata to the revision (D-3).

    Raises :class:`DxfExportError` if ``doc.audit()`` reports errors or fixes. The settings
    are computed first, so a naive ``frozen_at`` fails before the document is touched.
    """
    settings = repro_settings(plan_id, sha256, frozen_at)
    auditor = doc.audit()
    if auditor.has_errors or auditor.has_fixes:
        raise DxfExportError(
            f"DXF audit reported {len(auditor.errors)} errors and {len(auditor.fixes)} fixes"
        )
    pin_document(doc, settings)


def write_bytes(doc: Drawing) -> bytes:
    """DXF bytes of ``doc``: written to a text stream, then encoded (``\\n`` everywhere).

    Store them as they are (binary mode): a text-mode file would turn ``\\n`` into
    ``\\r\\n`` on Windows and change the hash.
    """
    stream = io.StringIO()
    doc.write(stream)
    return doc.encode(stream.getvalue())


def export_dxf(
    snapshot: dict[str, Any],
    catalog: CatalogDefinition,
    *,
    draft: bool,
    generator_version: str,
    frozen_at: datetime | None,
    dimension_tolerances: DimensionTolerances | None = None,
) -> bytes:
    """DXF R2013 bytes of ``snapshot`` (see :func:`build_document`).

    Byte-identical for the same snapshot, flags and ``frozen_at`` in any process, time zone
    or hash seed (with the pinned ezdxf 1.4.5 and Python 3.12).
    """
    doc = build_document(
        snapshot,
        catalog,
        draft=draft,
        generator_version=generator_version,
        frozen_at=frozen_at,
        dimension_tolerances=dimension_tolerances,
    )
    return write_bytes(doc)


def _add_entities(
    msp: Modelspace,
    entities: Iterable[Mapping[str, Any]],
    context: ExportContext,
    layers: frozenset[str],
) -> list[DXFGraphic]:
    written: list[DXFGraphic] = []
    skipped = 0
    for entity in entities:
        if entity["layer"] not in layers:
            raise DxfExportError(
                f"entity {entity['id']}: layer {entity['layer']!r} is not in catalog "
                f"{context.catalog.version!r}"
            )
        writer = ENTITY_WRITERS.get(entity["type"])
        if writer is None:
            if entity["type"] not in DEFERRED_TYPES:
                raise DxfExportError(f"entity {entity['id']}: unknown type {entity['type']!r}")
            skipped += 1
            continue
        try:
            graphics = writer(msp, entity, context)
        except (UnknownSymbolError, DegenerateDimensionError) as error:
            raise DxfExportError(f"entity {entity['id']}: {error}") from error
        for graphic in graphics:
            graphic.set_xdata(APP_ID, [(1000, f"entity_id={entity['id']}")])
            written.append(graphic)
    if skipped:
        logger.info("DXF export skipped %d entities without a writer", skipped)
    return written


def _add_watermark(msp: Modelspace, written: list[DXFGraphic], context: ExportContext) -> None:
    """``DRAFT — NOT VALIDATED`` on the sheet layer, at the lower left of the drawing."""
    msp.add_text(
        DRAFT_WATERMARK,
        height=paper_height_m(WATERMARK_HEIGHT_MM_PAPER, context.paper_scale),
        dxfattribs={"layer": SHEET_LAYER, "style": TEXT_STYLE, "insert": _lower_left(written)},
    )


def _lower_left(graphics: list[DXFGraphic]) -> Coordinate:
    box = bbox.extents(graphics, fast=True)
    if not box.has_data:
        return (0.0, 0.0)
    return (box.extmin.x, box.extmin.y)
