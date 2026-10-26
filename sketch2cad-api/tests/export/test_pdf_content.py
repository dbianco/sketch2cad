"""PDF notes and annex: scale bar, north arrow, legend, evidence table, image (REQ-13, 13.2).

Vectors and text are read back from the bytes with pypdfium2, as in ``test_pdf_layout``:
the scale bar and the north arrow are the path objects inside their notes regions
(:func:`~sketch2cad.export.pdf_legend.notes_layout`). Cross-process exports run
``export_with_annex`` in a fresh interpreter with another hash seed and time zone.
"""

# pypdfium2 ships without complete type information for its raw bindings.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import hashlib
import io
import math
import os
import subprocess
import sys
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import pytest
from dxf_snapshots import FROZEN_AT, entity, mixed_entities, snapshot
from PIL import Image
from test_pdf_layout import (
    API_DIR,
    EXPORT_DIR,
    LENGTH_TOLERANCE_PT,
    META,
    VERSION,
    measuring_snapshot,
    path_vertices,
)

from sketch2cad.catalog import default_catalog
from sketch2cad.export.pdf import PdfExportError, export_pdf, plan_page
from sketch2cad.export.pdf_annex import MAX_SIDE_PX, AnnexImage
from sketch2cad.export.pdf_legend import (
    north_direction,
    notes_layout,
    scale_bar_length_m,
    used_symbols,
)
from sketch2cad.export.pdf_sheet import PT_PER_MM, Rect

FRAME = {"origin_paper_mm": [10.0, 20.0], "x_axis_angle_deg": 0.0, "north_angle_deg": 33.7}
LABELS = ["Casquillo", "Mancha oscura", "Huella de calzado"]
EXPORT_SCRIPT = (
    "import sys; sys.path.insert(0, sys.argv[1]); import test_pdf_content as t; "
    "sys.stdout.buffer.write(t.export_with_annex(int(sys.argv[2])))"
)


def png(width: int = 320, height: int = 200, shade: int = 0) -> bytes:
    """A deterministic RGBA gradient PNG."""
    image = Image.new("RGBA", (width, height))
    image.putdata(
        [((x + shade) % 256, y % 256, 128, 255) for y in range(height) for x in range(width)]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def annex_of(data: bytes, label: Any = "original") -> AnnexImage:
    return AnnexImage(data, hashlib.sha256(data).hexdigest(), label)


def evidence_snapshot(count: int = 3, local_frame: Any = FRAME) -> dict[str, Any]:
    """Mixed entities (with a door) plus ``count`` evidence points and their table rows."""
    points: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for index in range(count):
        point = entity(20 + index, "point", "L_EVIDENCE", {"coords_m": [[0.5 * index, -0.004]]})
        evidence_id = f"e0000000-0000-4000-8000-{index + 1:012d}"
        points.append({**point, "evidence_id": evidence_id})
        rows.append(
            {
                "evidence_id": evidence_id,
                "evidence_number": index + 1,
                "entity_id": point["id"],
                "label": LABELS[index] if index < len(LABELS) else f"Indicio {index:03d}",
                "x_m": 0.5 * index,
                "y_m": -0.004,
                "metric_origin": "triangulation" if index == 1 else "scaled_drawing",
            }
        )
    snap = snapshot([*mixed_entities(), *points])
    return {**snap, "local_frame": local_frame, "evidence_table": rows}


def export(snap: dict[str, Any], **options: Any) -> bytes:
    kwargs: dict[str, Any] = {"draft": False, "frozen_at": FROZEN_AT, **options}
    return export_pdf(snap, default_catalog(), case_meta=META, generator_version=VERSION, **kwargs)


def export_with_annex(shade: int = 0) -> bytes:
    return export(evidence_snapshot(), annex=annex_of(png(shade=shade)))


def texts(data: bytes) -> list[str]:
    return [page.get_textpage().get_text_range() for page in pdfium.PdfDocument(data)]


def paths_in(data: bytes, region: Rect) -> list[list[tuple[float, float]]]:
    """Path objects of the first page whose points all lie inside ``region`` (mm)."""
    x0, y0, x1, y1 = (v * PT_PER_MM for v in (region.x, region.y, region.right, region.top))
    page = pdfium.PdfDocument(data)[0]
    found: list[list[tuple[float, float]]] = []
    for obj in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_PATH]):
        points = path_vertices(obj)
        if points and all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in points):
            found.append(points)
    return found


def image_px(obj: pdfium.PdfObject) -> tuple[int, int]:
    assert isinstance(obj, pdfium.PdfImage)
    return obj.get_px_size()


def notes_of(snap: dict[str, Any], **options: Any) -> Any:
    page = plan_page(snap, default_catalog(), **options)
    return notes_layout(page.layout.notes, len(used_symbols(snap, default_catalog())))


def test_text_has_every_evidence_number_label_and_the_annex_hash() -> None:
    data = png()
    sheet, annex = texts(export(evidence_snapshot(), annex=annex_of(data)))
    words = sheet.split()
    for number, label in zip(("1", "2", "3"), LABELS, strict=True):
        assert number in words and label in sheet
    for header in ("Tabla de indicios", "Nº", "Etiqueta", "X (m)", "Y (m)", "Origen métrico"):
        assert header in sheet
    assert "0,50" in words and "0,00" in words and "-0,00" not in words
    assert "Triangulación" in sheet and "Dibujo a escala" in sheet
    assert annex.count(hashlib.sha256(data).hexdigest()) == 1
    assert "Anexo: croquis original" in annex and "Dimensiones: 320 × 200 px" in annex


@pytest.mark.parametrize(
    ("scale", "metres"), [(50, 2.0), (100, 5.0), (200, 10.0), (250, 10.0), (500, 20.0)]
)
def test_scale_bar_uses_a_round_length_of_20_to_50_mm(scale: int, metres: float) -> None:
    length = scale_bar_length_m(scale)
    assert length == metres and 20.0 <= length * 1000 / scale <= 50.0


@pytest.mark.parametrize("sheet", ["A3", "A4"])
def test_scale_bar_of_5_m_at_1_100_measures_50_mm(sheet: str) -> None:
    snap = measuring_snapshot()
    data = export(snap, sheet=sheet, scale=100)
    paths = paths_in(data, notes_of(snap, sheet=sheet, scale=100).scale_bar)
    xs = [x for path in paths for x, _ in path]
    assert max(xs) - min(xs) == pytest.approx(50.0 * PT_PER_MM, abs=LENGTH_TOLERANCE_PT)
    sheet_text = texts(data)[0]
    assert "Escala gráfica" in sheet_text and "5 m" in sheet_text


@pytest.mark.parametrize("angle", [0.0, 90.0, 33.7])
def test_north_arrow_points_along_the_north_angle(angle: float) -> None:
    snap = evidence_snapshot(local_frame={**FRAME, "north_angle_deg": angle})
    paths = paths_in(export(snap), notes_of(snap).north)
    (outline,) = [path for path in paths if len(path) >= 4]  # tip, right, notch, left
    (tx, ty), (nx, ny) = outline[0], outline[2]
    length = math.hypot(tx - nx, ty - ny)
    expected = (-math.sin(math.radians(angle)), math.cos(math.radians(angle)))
    assert north_direction(angle) == pytest.approx(expected)
    assert ((tx - nx) / length, (ty - ny) / length) == pytest.approx(expected, abs=1e-4)


def test_without_a_local_frame_the_north_is_not_drawn() -> None:
    snap = evidence_snapshot(local_frame=None)
    data = export(snap)
    assert paths_in(data, notes_of(snap).north) == []
    assert "Norte sin definir" in texts(data)[0]


def test_exactly_one_annex_page_with_one_image() -> None:
    with_annex = pdfium.PdfDocument(export(evidence_snapshot(), annex=annex_of(png())))
    without = pdfium.PdfDocument(export(evidence_snapshot()))
    assert (len(with_annex), len(without)) == (2, 1)
    images = [list(page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE])) for page in with_annex]
    assert [len(page) for page in images] == [0, 1]
    assert image_px(images[1][0]) == (320, 200)


def test_legend_lists_exactly_the_used_symbols() -> None:
    sheet = texts(export(evidence_snapshot()))[0]
    used = {"Puerta", "Marcador de indicio"}
    assert [s.label for s in used_symbols(evidence_snapshot(), default_catalog())] == [
        s.label for s in default_catalog().symbols if s.label in used
    ]
    assert "Leyenda" in sheet
    for symbol in default_catalog().symbols:
        assert (symbol.label in sheet) == (symbol.label in used), symbol.label
    assert "Leyenda" not in texts(export(measuring_snapshot()))[0]


def test_rows_that_do_not_fit_continue_on_the_annex() -> None:
    snap = evidence_snapshot(count=40)
    sheet, annex = texts(export(snap, sheet="A4", annex=annex_of(png())))
    assert "indicios más continúan en el anexo" in sheet
    assert "Tabla de indicios (continuación)" in annex
    labels = [f"Indicio {index:03d}" for index in range(3, 40)]
    on_annex = [label for label in labels if label in annex]  # the plan prints every label
    assert on_annex == labels[len(labels) - len(on_annex) :] and 0 < len(on_annex) < 37
    assert f"… {len(on_annex)} indicios más continúan en el anexo." in sheet
    alone = texts(export(snap, sheet="A4"))
    assert len(alone) == 1 and "indicios más no caben en la hoja" in alone[0]


def test_large_images_are_downscaled_and_rectified_is_labelled() -> None:
    data = export(evidence_snapshot(), annex=annex_of(png(2500, 1000), "rectified"))
    page = pdfium.PdfDocument(data)[1]
    (image,) = page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE])
    assert image_px(image) == (MAX_SIDE_PX, 800)
    annex = texts(data)[1]
    assert "Anexo: croquis rectificado" in annex and "2500 × 1000 px" in annex


def test_annex_with_a_wrong_hash_or_unreadable_bytes_is_rejected() -> None:
    with pytest.raises(PdfExportError, match="SHA-256"):
        export(evidence_snapshot(), annex=AnnexImage(png(), "0" * 64))
    with pytest.raises(PdfExportError, match="decoded"):
        export(evidence_snapshot(), annex=annex_of(b"%PDF-1.7 not an image"))


def test_annex_export_is_the_same_in_two_processes_and_follows_the_image() -> None:
    def run(seed: str, extra: dict[str, str]) -> bytes:
        env = {**os.environ, "PYTHONHASHSEED": seed, **extra}
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-P", "-c", EXPORT_SCRIPT, str(EXPORT_DIR), "0"],
            capture_output=True,
            check=True,
            cwd=API_DIR,
            env=env,
            timeout=120,
        ).stdout

    first = run("2", {})
    second = run("5", {"TZ": "Asia/Tokyo", "SOURCE_DATE_EPOCH": "1600000000"})
    digest = hashlib.sha256(first).hexdigest()
    assert first.startswith(b"%PDF-")
    assert digest == hashlib.sha256(second).hexdigest()
    assert digest == hashlib.sha256(export_with_annex()).hexdigest()
    assert digest != hashlib.sha256(export_with_annex(shade=1)).hexdigest()
