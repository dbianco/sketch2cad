"""Draft watermark, no hashes in drafts and the legal disclaimer of finals (REQ-13, REQ-14, H4-7).

Texts are read back with pypdfium2; the disclaimer may be wrapped, so it is matched after
collapsing whitespace (:func:`flat`). Cross-process exports run :func:`export_variant` in a
fresh interpreter with another hash seed and time zone.
"""

# pypdfium2 ships without complete type information for its raw bindings.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import hashlib
import io
import os
import subprocess
import sys
from typing import Any

import pypdfium2 as pdfium
import pytest
from dxf_snapshots import FROZEN_AT, mixed_entities, snapshot
from ezdxf.filemanagement import read as read_drawing
from pydantic import ValidationError
from test_pdf_content import annex_of, evidence_snapshot, export, png, texts
from test_pdf_layout import API_DIR, EXPORT_DIR, VERSION

from sketch2cad.catalog import default_catalog
from sketch2cad.export.disclaimer import (
    DISCLAIMER_SETTING,
    DISCLAIMER_TEXT,
    ExportLegal,
    disclaimer_text,
)
from sketch2cad.export.dxf import DRAFT_WATERMARK, SHEET_LAYER, export_dxf
from sketch2cad.export.pdf import plan_page
from sketch2cad.export.pdf_sheet import PT_PER_MM
from sketch2cad.export.pdf_watermark import WATERMARK_TEXT
from sketch2cad.revisions.snapshot import snapshot_sha256

EXACT_SENTENCE = "Borrador técnico, no apto como prueba hasta contar con revisión jurídica"
EXPORT_SCRIPT = (
    "import sys; sys.path.insert(0, sys.argv[1]); import test_draft_disclaimer as t; "
    "sys.stdout.buffer.write(t.export_variant(sys.argv[2]))"
)


def flat(text: str) -> str:
    """``text`` with every run of whitespace (wrapped lines included) as one space."""
    return " ".join(text.split())


def export_variant(variant: str) -> bytes:
    """``draft`` or ``final`` export of the evidence snapshot with an annex image."""
    draft = variant == "draft"
    return export(
        evidence_snapshot(),
        draft=draft,
        frozen_at=None if draft else FROZEN_AT,
        annex=annex_of(png()),
    )


def test_disclaimer_text_is_the_exact_sentence_and_can_be_disabled() -> None:
    assert DISCLAIMER_TEXT == EXACT_SENTENCE
    assert disclaimer_text(True) == EXACT_SENTENCE and disclaimer_text(False) is None
    assert ExportLegal().disclaimer == EXACT_SENTENCE
    assert ExportLegal(disclaimer_enabled=False).disclaimer is None


def test_draft_pdf_has_the_watermark_on_every_page_and_no_hashes() -> None:
    snap = evidence_snapshot()
    image = png()
    data = export(snap, draft=True, frozen_at=None, annex=annex_of(image))
    pages = texts(data)
    assert len(pages) == 2
    for page in pages:
        assert "BORRADOR" in page and "DRAFT" in page and WATERMARK_TEXT in flat(page)
    sheet, annex = pages
    full = snapshot_sha256(snap)
    for page in pages:
        assert full not in page and full[:12] not in page
        assert hashlib.sha256(image).hexdigest() not in page and "SHA-256" not in page
    assert "Hash" in sheet and "no aplica (borrador)" in sheet
    assert "Hash no incluido en borradores" in annex
    assert EXACT_SENTENCE not in flat(sheet)  # the watermark already says it is a draft
    subject = pdfium.PdfDocument(data).get_metadata_dict()["Subject"]
    assert "snapshot_sha256" not in subject and full not in subject


@pytest.mark.parametrize("sheet", ["A4", "A3"])
def test_final_pdf_prints_the_disclaimer_and_the_hashes_without_watermark(sheet: str) -> None:
    snap = evidence_snapshot()
    image = png()
    pages = texts(export(snap, sheet=sheet, annex=annex_of(image)))
    assert flat(pages[0]).count(EXACT_SENTENCE) == 1
    assert snapshot_sha256(snap)[:12] in pages[0] and "Snapshot SHA-256" in pages[0]
    assert f"SHA-256: {hashlib.sha256(image).hexdigest()}" in pages[1]
    for page in pages:
        assert "BORRADOR" not in page and "DRAFT" not in page and "no aplica" not in page


def test_disabled_disclaimer_is_absent_from_the_final_pdf() -> None:
    snap = evidence_snapshot()
    disabled = export(snap, legal=ExportLegal(disclaimer_enabled=False))
    sheet = flat(texts(disabled)[0])
    assert "Borrador técnico" not in sheet and "revisión jurídica" not in sheet
    assert snapshot_sha256(snap)[:12] in sheet
    assert disabled != export(snap)
    assert export(snap, legal=ExportLegal()) == export(snap)


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("0", False), ("true", True)])
def test_environment_variable_switches_the_disclaimer(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv(DISCLAIMER_SETTING, value)
    legal = ExportLegal.from_environment()
    assert legal.disclaimer_enabled is expected
    sheet = flat(texts(export(evidence_snapshot(), legal=legal))[0])
    assert (EXACT_SENTENCE in sheet) is expected


def test_disclaimer_defaults_to_enabled_and_rejects_non_booleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DISCLAIMER_SETTING, raising=False)
    assert ExportLegal.from_environment() == ExportLegal(disclaimer_enabled=True)
    monkeypatch.setenv(DISCLAIMER_SETTING, "maybe")
    with pytest.raises(ValidationError):
        ExportLegal.from_environment()


def test_disclaimer_sits_between_the_evidence_table_and_the_title_block() -> None:
    snap = evidence_snapshot(count=40)
    data = export(snap, sheet="A4")
    textpage = pdfium.PdfDocument(data)[0].get_textpage()

    def box(needle: str) -> tuple[float, float, float, float]:
        found = textpage.search(needle).get_next()
        assert found is not None, needle
        return textpage.get_charbox(found[0])

    disclaimer = box("Borrador")
    overflow = box("indicios más")
    title_top = plan_page(snap, default_catalog(), sheet="A4").layout.title_block.top * PT_PER_MM
    assert disclaimer[3] < overflow[1] and disclaimer[1] > title_top


def read_dxf(data: bytes) -> Any:
    return read_drawing(io.StringIO(data.decode("utf-8")))


def sheet_texts(data: bytes) -> list[str]:
    msp = read_dxf(data).modelspace()
    return [text.dxf.text for text in msp.query(f'TEXT[layer=="{SHEET_LAYER}"]')]


def test_dxf_draft_keeps_its_watermark_and_no_dxf_carries_the_disclaimer() -> None:
    snap = snapshot(mixed_entities())
    catalog = default_catalog()
    draft = export_dxf(snap, catalog, draft=True, generator_version=VERSION, frozen_at=None)
    final = export_dxf(snap, catalog, draft=False, generator_version=VERSION, frozen_at=FROZEN_AT)
    assert sheet_texts(draft) == [DRAFT_WATERMARK] and sheet_texts(final) == []
    for data in (draft, final):
        assert "revisión jurídica".encode() not in data and b"BORRADOR" not in data


def test_draft_and_final_pdfs_are_the_same_in_two_processes() -> None:
    def run(variant: str, seed: str, extra: dict[str, str]) -> bytes:
        env = {**os.environ, "PYTHONHASHSEED": seed, **extra}
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-P", "-c", EXPORT_SCRIPT, str(EXPORT_DIR), variant],
            capture_output=True,
            check=True,
            cwd=API_DIR,
            env=env,
            timeout=120,
        ).stdout

    digests: dict[str, str] = {}
    for variant, seed, extra in (
        ("draft", "3", {"TZ": "Europe/Madrid"}),
        ("final", "6", {"TZ": "Asia/Tokyo", "SOURCE_DATE_EPOCH": "1650000000"}),
    ):
        other = run(variant, seed, extra)
        assert other.startswith(b"%PDF-")
        digests[variant] = hashlib.sha256(other).hexdigest()
        assert digests[variant] == hashlib.sha256(export_variant(variant)).hexdigest()
    assert digests["draft"] != digests["final"]
