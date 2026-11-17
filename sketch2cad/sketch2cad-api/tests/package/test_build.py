"""Final package: members, manifest hashes, signature, audit report and determinism (REQ-14, US-16).

Snapshots come from ``tests/export/dxf_snapshots.py``, loaded by path because this folder is
not on the import path of that helper. The signing key is generated under ``tmp_path``.
Cross-process builds run :func:`build_from_key_file` in a fresh interpreter with another
hash seed and time zone.
"""

# pypdfium2 ships without complete type information for its raw bindings.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pypdfium2 as pdfium
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from sketch2cad.audit.verify import genesis_hash_of, recompute_hash
from sketch2cad.catalog import default_catalog
from sketch2cad.export.disclaimer import DISCLAIMER_TEXT, ExportLegal
from sketch2cad.export.pdf_annex import ANNEX_TITLES
from sketch2cad.export.pdf_sheet import CaseMeta
from sketch2cad.package.build import (
    ARTIFACT_KINDS,
    PackageError,
    PackageInputs,
    SourceFile,
    build_package,
)
from sketch2cad.package.manifest import Actor, SoftwareInfo, canonical_manifest_bytes
from sketch2cad.package.signing import (
    generate_signing_key,
    load_public_key,
    load_signing_key,
    verify_manifest,
)
from sketch2cad.revisions.snapshot import snapshot_sha256
from sketch2cad.sources.raster import rasterize_pdf

PACKAGE_DIR = Path(__file__).resolve().parent
API_DIR = PACKAGE_DIR.parents[1]
EXPORT_TESTS_DIR = PACKAGE_DIR.parent / "export"
CASE_FILE_ID = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b"
REVISION_ID = "6f7a8b9c-0d1e-4f2a-9b3c-4d5e6f7a8b9c"
EXPORTED_AT = datetime(2026, 10, 22, 8, 0, 0, tzinfo=UTC)
EXPECTED_NAMES = {
    "manifest.json",
    "plan.dxf",
    "plan.pdf",
    "audit.pdf",
    "original.png",
    "events.json",
    "snapshot.json",
    "VERIFY.md",
    "public_key.pem",
}
BUILD_SCRIPT = (
    "import sys; sys.path.insert(0, sys.argv[1]); import test_build as t; "
    "sys.stdout.buffer.write(t.build_from_key_file(sys.argv[2]))"
)


def _load_helper(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, EXPORT_TESTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_snapshots = _load_helper("dxf_snapshots")
FROZEN_AT = cast(datetime, _snapshots.FROZEN_AT)
PLAN_ID = cast(str, _snapshots.PLAN_ID)
USER_ID = cast(str, _snapshots.USER_ID)
make_snapshot = cast(Callable[[list[dict[str, Any]]], dict[str, Any]], _snapshots.snapshot)
mixed_entities = cast(Callable[[], list[dict[str, Any]]], _snapshots.mixed_entities)


def flat(text: str) -> str:
    """``text`` with every run of whitespace (wrapped lines included) as one space."""
    return " ".join(text.split())


def pdf_texts(data: bytes) -> list[str]:
    document = pdfium.PdfDocument(data)
    try:
        return [flat(page.get_textpage().get_text_range()) for page in document]
    finally:
        document.close()


def png(width: int = 240, height: int = 160) -> bytes:
    image = Image.new("RGB", (width, height))
    image.putdata([(x % 256, y % 256, 90) for y in range(height) for x in range(width)])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def one_page_pdf() -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(200, 150), invariant=1)
    canvas.line(10, 10, 190, 140)
    canvas.drawString(20, 120, "Croquis")
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def events_chain(count: int = 6) -> list[dict[str, Any]]:
    """A valid case chain: ``case_file.created`` then ``count - 1`` events of the plan."""
    kinds = ("entity.created", "entity.updated", "plan.transition", "lease.acquired")
    events: list[dict[str, Any]] = []
    prev = genesis_hash_of(CASE_FILE_ID)
    for index in range(count):
        first = index == 0
        event: dict[str, Any] = {
            "id": 10 + index * 3,
            "case_file_id": CASE_FILE_ID,
            "plan_id": None if first else PLAN_ID,
            "revision_id": None if first else REVISION_ID,
            "actor_id": USER_ID,
            "actor_role": "technician",
            "kind": "case_file.created" if first else kinds[index % len(kinds)],
            "entity_id": None,
            "before": None if first else {"status": "EDITING"},
            "after": {"code": "EXP-2026-001"} if first else {"operation": "move", "n": index},
            "reason": None,
            "client_ip": "127.0.0.1",
            "occurred_at": f"2026-10-21T08:{index // 60:02d}:{index % 60:02d}.000000Z",
            "prev_hash": prev,
        }
        event["hash"] = recompute_hash(event)
        prev = event["hash"]
        events.append(event)
    return events


def package_inputs(key: Ed25519PrivateKey, **changes: Any) -> PackageInputs:
    snap = make_snapshot(mixed_entities())
    data = png()
    inputs = PackageInputs(
        snapshot=snap,
        snapshot_sha256=snapshot_sha256(snap),
        catalog=default_catalog(),
        case_meta=CaseMeta(
            "EXP-2026-001", "Robo en vivienda", "Robo", date(2026, 10, 19), "Ana Técnica", "Luis"
        ),
        case_file_id=CASE_FILE_ID,
        plan_id=PLAN_ID,
        revision_id=REVISION_ID,
        frozen_at=FROZEN_AT,
        validated_by=Actor(USER_ID, "Luis Revisor", "reviewer"),
        validated_at=FROZEN_AT,
        exported_by=Actor(USER_ID, "Luis Revisor", "reviewer"),
        exported_at=EXPORTED_AT,
        source=SourceFile(data, hashlib.sha256(data).hexdigest(), "image/png", "croquis 1.png"),
        events=events_chain(),
        calibration=snap["calibration"],
        local_frame={"origin_paper_mm": [10.0, 20.0], "x_axis_angle_deg": 0.0},
        software=SoftwareInfo("0.1.0", "abc1234", "pipeline-1", None),
        signing_key=key,
        timezone_display="America/Panama",
    )
    return replace(inputs, **changes)


def build_from_key_file(key_path: str) -> bytes:
    """ZIP bytes of the default package with a long event chain (cross-process check)."""
    key = load_signing_key(key_path)
    return build_package(package_inputs(key, events=events_chain(150))).zip_bytes


def members(zip_bytes: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    path = tmp_path / "keys" / "signing-key.pem"
    generate_signing_key(path)
    return path


@pytest.fixture
def key(key_path: Path) -> Ed25519PrivateKey:
    return load_signing_key(key_path)


def test_zip_holds_exactly_the_six_artifacts_verify_md_and_the_public_key(
    key: Ed25519PrivateKey,
) -> None:
    result = build_package(package_inputs(key))
    files = members(result.zip_bytes)
    assert set(files) == EXPECTED_NAMES and len(files) == len(EXPECTED_NAMES)
    assert list(files) == list(result.files)
    manifest = json.loads(files["manifest.json"])
    assert [item["kind"] for item in manifest["artifacts"]] == list(ARTIFACT_KINDS)
    artifact_names = {item["filename"] for item in manifest["artifacts"]}
    assert artifact_names == EXPECTED_NAMES - {"manifest.json", "VERIFY.md", "public_key.pem"}
    assert files["manifest.json"] == canonical_manifest_bytes(result.manifest)
    assert files["original.png"] == package_inputs(key).source.data
    verify_md = files["VERIFY.md"].decode("utf-8")
    assert "sketch2cad-verify" in verify_md and "sha256sum" in verify_md
    assert "integridad y origen técnico" in verify_md and "No prueba autoría pericial" in verify_md
    for info in zipfile.ZipFile(io.BytesIO(result.zip_bytes)).infolist():
        assert info.compress_type == zipfile.ZIP_STORED and info.extra == b""
        assert info.date_time == (2026, 10, 21, 9, 30, 14)
        assert info.external_attr >> 16 == 0o100644


def test_every_manifest_hash_matches_its_zip_member_and_the_snapshot(
    key: Ed25519PrivateKey,
) -> None:
    inputs = package_inputs(key)
    files = members(build_package(inputs).zip_bytes)
    manifest = json.loads(files["manifest.json"])
    for item in manifest["artifacts"]:
        assert item["sha256"] == hashlib.sha256(files[item["filename"]]).hexdigest()
    assert manifest["revision"]["snapshot_sha256"] == inputs.snapshot_sha256
    assert hashlib.sha256(files["snapshot.json"]).hexdigest() == inputs.snapshot_sha256
    assert json.loads(files["events.json"]) == inputs.events
    assert manifest["events_chain"] == {"count": 6, "last_hash": inputs.events[-1]["hash"]}


def test_signature_verifies_with_the_included_key_and_fails_after_any_hashed_byte_changes(
    key: Ed25519PrivateKey,
) -> None:
    files = members(build_package(package_inputs(key)).zip_bytes)
    manifest = json.loads(files["manifest.json"])
    public_key = load_public_key(files["public_key.pem"])
    assert verify_manifest(manifest, public_key).valid
    for item in manifest["artifacts"]:
        tampered = bytearray(files[item["filename"]])
        tampered[len(tampered) // 2] ^= 0x01
        digest = hashlib.sha256(bytes(tampered)).hexdigest()
        assert digest != item["sha256"]
        forged = json.loads(files["manifest.json"])
        for entry in forged["artifacts"]:
            if entry["filename"] == item["filename"]:
                entry["sha256"] = digest
        assert not verify_manifest(forged, public_key).valid, item["filename"]


def test_audit_report_states_the_signature_scope_the_disclaimer_and_the_contents(
    key: Ed25519PrivateKey,
) -> None:
    result = build_package(package_inputs(key))
    text = " ".join(pdf_texts(result.files["audit.pdf"]))
    assert "integridad y origen técnico" in text and "no autoría pericial" in text
    assert DISCLAIMER_TEXT in text
    for item in result.manifest["artifacts"]:
        if item["kind"] != "pdf_audit":
            assert item["sha256"] in text and item["filename"] in text
    assert "Nota de validez" in text and "sketch2cad-verify" in text
    assert "Eventos del plano (5)" in text and "plan.transition" in text
    assert result.manifest["events_chain"]["last_hash"] in text
    assert DISCLAIMER_TEXT in " ".join(pdf_texts(result.files["plan.pdf"]))


def test_disabled_disclaimer_leaves_audit_and_plan_without_the_sentence(
    key: Ed25519PrivateKey,
) -> None:
    result = build_package(package_inputs(key, legal=ExportLegal(disclaimer_enabled=False)))
    audit = " ".join(pdf_texts(result.files["audit.pdf"]))
    assert DISCLAIMER_TEXT not in audit
    assert "integridad y origen técnico" in audit and "no autoría pericial" in audit
    assert DISCLAIMER_TEXT not in " ".join(pdf_texts(result.files["plan.pdf"]))


def test_identical_inputs_give_identical_zip_bytes_across_processes(key_path: Path) -> None:
    local = build_from_key_file(str(key_path))
    assert local == build_from_key_file(str(key_path))
    audit = members(local)["audit.pdf"]
    assert len(pdf_texts(audit)) > 1  # 149 plan events paginate
    for seed, zone in (("1", "America/Panama"), ("4242", "Asia/Tokyo")):
        env = {**os.environ, "PYTHONHASHSEED": seed, "TZ": zone}
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
            [sys.executable, "-P", "-c", BUILD_SCRIPT, str(PACKAGE_DIR), str(key_path)],
            capture_output=True,
            check=True,
            cwd=API_DIR,
            env=env,
            timeout=120,
        )
        assert completed.stdout == local


def test_pdf_original_ships_as_is_and_its_rasterized_page_is_the_annex(
    key: Ed25519PrivateKey,
) -> None:
    original = one_page_pdf()
    raster = rasterize_pdf(original, dpi=72).png
    source = SourceFile(
        original, hashlib.sha256(original).hexdigest(), "application/pdf", "croquis.pdf", raster
    )
    result = build_package(package_inputs(key, source=source))
    files = members(result.zip_bytes)
    assert "original.pdf" in files and "original.png" not in files
    assert files["original.pdf"] == original
    entry = next(i for i in result.manifest["artifacts"] if i["kind"] == "source_original")
    assert entry == {
        "kind": "source_original",
        "filename": "original.pdf",
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    annex = pdf_texts(files["plan.pdf"])[1]
    assert ANNEX_TITLES["original_page"] == "Anexo: página del original (PDF) rasterizada"
    assert ANNEX_TITLES["original_page"] in annex
    assert hashlib.sha256(raster).hexdigest() in annex
    with pytest.raises(PackageError, match="rasterized page"):
        build_package(package_inputs(key, source=replace(source, raster_png=None)))


def test_broken_event_chain_raises(key: Ed25519PrivateKey) -> None:
    events = events_chain()
    events[3] = {**events[3], "after": {"operation": "forged"}}
    with pytest.raises(PackageError, match="broken at event 19"):
        build_package(package_inputs(key, events=events))
    with pytest.raises(PackageError, match="empty"):
        build_package(package_inputs(key, events=[]))


def test_unfrozen_revision_or_wrong_hashes_raise(key: Ed25519PrivateKey) -> None:
    with pytest.raises(PackageError, match="frozen revision"):
        build_package(package_inputs(key, frozen_at=None))
    with pytest.raises(PackageError, match="snapshot_sha256"):
        build_package(package_inputs(key, snapshot_sha256="0" * 64))
    inputs = package_inputs(key)
    with pytest.raises(PackageError, match="original bytes"):
        build_package(replace(inputs, source=replace(inputs.source, sha256="0" * 64)))
