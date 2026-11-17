"""Standalone verifier ``verifier/sketch2cad_verify.py`` (REQ-14, US-17, REQ-11, CA-09, H4-5).

Packages come from :func:`sketch2cad.package.build.build_package` with the inputs of
``test_build.py``. The verifier runs as a subprocess, as an operator would run it; the
divergence tests load it in process to compare its copied chain and signature checks with
``sketch2cad.audit.verify`` and ``sketch2cad.package.signing``.
"""

import ast
import base64
import importlib.metadata
import importlib.util
import io
import json
import re
import subprocess
import sys
import warnings
import zipfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_build import events_chain, members, package_inputs

from sketch2cad.audit.verify import recompute_hash, verify_chain
from sketch2cad.package.build import build_package
from sketch2cad.package.manifest import canonical_manifest_bytes, sha256_hex
from sketch2cad.package.signing import (
    public_key_id,
    public_key_pem,
    sign_manifest,
    verify_manifest,
)

API_DIR = Path(__file__).resolve().parents[2]
VERIFIER = API_DIR / "verifier" / "sketch2cad_verify.py"
ALLOWED_THIRD_PARTY = {"cryptography", "rfc8785"}
RESTRICTED_LAUNCHER = """
import importlib.util, runpy, sys
shim, script, *args = sys.argv[1:]
sys.path.append(shim)
for name in ("sketch2cad", "numpy", "PIL", "ezdxf"):
    if importlib.util.find_spec(name) is not None:
        sys.exit(f"{name} is importable in the restricted environment")
sys.argv = [script, *args]
runpy.run_path(script, run_name="__main__")
"""


def load_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sketch2cad_verify", VERIFIER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("sketch2cad_verify", module)
    spec.loader.exec_module(module)
    return module


verifier = load_verifier()


@dataclass(frozen=True)
class Built:
    zip_bytes: bytes
    key: Ed25519PrivateKey


@pytest.fixture(scope="module")
def built() -> Built:
    key = Ed25519PrivateKey.generate()
    return Built(build_package(package_inputs(key)).zip_bytes, key)


def rewrite(
    zip_bytes: bytes,
    changes: Mapping[str, bytes | None] | None = None,
    extra: Mapping[str, bytes] | None = None,
) -> bytes:
    """The ZIP with members replaced (``None`` drops one) and ``extra`` appended."""
    changes = changes or {}
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(zip_bytes)) as source,
        zipfile.ZipFile(buffer, "w") as target,
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")  # duplicate names are written on purpose
        for info in source.infolist():
            data = changes.get(info.filename, source.read(info))
            if data is not None:
                target.writestr(info, data)
        for name, data in (extra or {}).items():
            target.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), data)
    return buffer.getvalue()


def flip(data: bytes, position: int) -> bytes:
    tampered = bytearray(data)
    tampered[position] ^= 0x01
    return bytes(tampered)


def run_verifier(zip_bytes: bytes, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "paquete.zip"
    path.write_bytes(zip_bytes)
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(VERIFIER), str(path), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        check=False,
    )


def resign(
    zip_bytes: bytes, key: Ed25519PrivateKey, edit: Callable[[dict[str, Any]], None]
) -> bytes:
    """Edit the manifest and sign it with ``key``, shipping that key's public key."""
    manifest = json.loads(members(zip_bytes)["manifest.json"])
    edit(manifest)
    signed = sign_manifest(manifest, key)
    return rewrite(
        zip_bytes,
        {
            "manifest.json": canonical_manifest_bytes(signed),
            "public_key.pem": public_key_pem(key.public_key()),
        },
    )


def truncated_events(zip_bytes: bytes) -> tuple[bytes, str]:
    """``events.json`` without its last event, and the new SHA-256."""
    events = json.loads(members(zip_bytes)["events.json"])
    data = rfc8785.dumps(events[:-1])
    return data, sha256_hex(data)


def set_artifact_hash(manifest: dict[str, Any], filename: str, digest: str) -> None:
    for item in manifest["artifacts"]:
        if item["filename"] == filename:
            item["sha256"] = digest


# --- Fresh package -------------------------------------------------------------------------


def test_fresh_package_verifies_with_exit_0_and_warns_about_the_embedded_key(
    built: Built, tmp_path: Path
) -> None:
    completed = run_verifier(built.zip_bytes, tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    out = completed.stdout
    for number in range(1, 7):
        assert f"[OK]   {number}." in out
    assert "[FAIL]" not in out and "RESULTADO: OK" in out
    assert "AVISO" in out and "--public-key" in out


def test_operator_key_or_pinned_id_removes_the_notice(built: Built, tmp_path: Path) -> None:
    pem = tmp_path / "institucion.pem"
    pem.write_bytes(public_key_pem(built.key.public_key()))
    for args in (("--public-key", str(pem)), ("--key-id", public_key_id(built.key.public_key()))):
        completed = run_verifier(built.zip_bytes, tmp_path, *args)
        assert completed.returncode == 0, completed.stdout
        assert "AVISO" not in completed.stdout + completed.stderr


def test_json_and_quiet_outputs(built: Built, tmp_path: Path) -> None:
    report = json.loads(run_verifier(built.zip_bytes, tmp_path, "--json").stdout)
    assert report["ok"] is True and report["exit_code"] == 0
    assert [check["status"] for check in report["checks"]] == ["ok"] * 6
    assert report["public_key_source"] == "package" and len(report["warnings"]) == 1
    quiet = run_verifier(built.zip_bytes, tmp_path, "--quiet")
    assert quiet.returncode == 0 and quiet.stdout.strip().startswith("RESULTADO: OK")
    assert "AVISO" in quiet.stderr
    broken = rewrite(built.zip_bytes, {"plan.dxf": b"0\nEOF\n"})
    report = json.loads(run_verifier(broken, tmp_path, "--json").stdout)
    assert report["ok"] is False and report["exit_code"] == 1
    assert report["checks"][3]["failures"][0]["target"] == "plan.dxf"


# --- One byte altered at ten-plus positions (CA-09, H4-5) ---------------------------------


def _after(marker: bytes, offset: int, occurrence: int = 1) -> Callable[[bytes], int]:
    def locate(data: bytes) -> int:
        position = -1
        for _ in range(occurrence):
            position = data.index(marker, position + 1)
        return position + len(marker) + offset

    return locate


def _middle(data: bytes) -> int:
    return len(data) // 2


TAMPER_POSITIONS: list[tuple[str, Callable[[bytes], int], str | None]] = [
    ("manifest.json", lambda _: 0, None),
    ("manifest.json", _after(b'"sha256":"', 10), None),
    ("plan.dxf", _middle, None),
    ("plan.pdf", _middle, None),
    ("audit.pdf", _middle, None),
    ("original.png", _middle, None),
    ("snapshot.json", _middle, None),
    ("events.json", lambda _: 0, None),
    ("events.json", _after(b'"hash":"', 20, occurrence=2), "evento id=13"),
    ("events.json", _after(b'"n":', 0, occurrence=3), "evento id=19"),
    ("public_key.pem", _after(b"-----\n", 30), None),
]


@pytest.mark.parametrize(
    ("member", "locate", "event"),
    TAMPER_POSITIONS,
    ids=[f"{member}-{index}" for index, (member, _, _) in enumerate(TAMPER_POSITIONS)],
)
def test_one_altered_byte_fails_with_exit_1_naming_the_member(
    built: Built,
    tmp_path: Path,
    member: str,
    locate: Callable[[bytes], int],
    event: str | None,
) -> None:
    original = members(built.zip_bytes)[member]
    tampered = flip(original, locate(original))
    completed = run_verifier(rewrite(built.zip_bytes, {member: tampered}), tmp_path)
    assert completed.returncode == 1, completed.stdout
    assert "RESULTADO: FALLO" in completed.stdout
    failure_lines = [line for line in completed.stdout.splitlines() if line.startswith("       - ")]
    assert any(member in line for line in failure_lines), completed.stdout
    if event is not None:
        assert f"events.json: {event}" in completed.stdout


# --- Truncation, re-signing and key pinning ----------------------------------------------


def test_truncated_tail_with_fixed_hash_breaks_the_signature_and_the_count(
    built: Built, tmp_path: Path
) -> None:
    data, digest = truncated_events(built.zip_bytes)
    manifest = json.loads(members(built.zip_bytes)["manifest.json"])
    set_artifact_hash(manifest, "events.json", digest)
    tampered = rewrite(
        built.zip_bytes,
        {"events.json": data, "manifest.json": canonical_manifest_bytes(manifest)},
    )
    completed = run_verifier(tampered, tmp_path)
    assert completed.returncode == 1
    assert "la firma no corresponde al manifiesto" in completed.stdout
    assert "cadena truncada" in completed.stdout


def test_resigned_truncation_is_caught_by_the_count_or_by_key_pinning(
    built: Built, tmp_path: Path
) -> None:
    attacker = Ed25519PrivateKey.generate()
    data, digest = truncated_events(built.zip_bytes)
    with_events = rewrite(built.zip_bytes, {"events.json": data})
    stale_count = resign(
        with_events, attacker, lambda m: set_artifact_hash(m, "events.json", digest)
    )
    completed = run_verifier(stale_count, tmp_path)
    assert completed.returncode == 1 and "cadena truncada" in completed.stdout

    last_hash = json.loads(data)[-1]["hash"]

    def forge(manifest: dict[str, Any]) -> None:
        set_artifact_hash(manifest, "events.json", digest)
        manifest["events_chain"] = {"count": 5, "last_hash": last_hash}

    forged = resign(with_events, attacker, forge)
    unpinned = run_verifier(forged, tmp_path)
    assert unpinned.returncode == 0 and "AVISO" in unpinned.stdout  # internal consistency only
    pem = tmp_path / "institucion.pem"
    pem.write_bytes(public_key_pem(built.key.public_key()))
    pinned = run_verifier(forged, tmp_path, "--public-key", str(pem))
    assert pinned.returncode == 1 and "manifest.json" in pinned.stdout
    assert public_key_id(attacker.public_key()) in pinned.stdout
    by_id = run_verifier(forged, tmp_path, "--key-id", public_key_id(built.key.public_key()))
    assert by_id.returncode == 1 and "distinto del fijado" in by_id.stdout


# --- ZIP structure -----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["../evil.txt", "/etc/evil.txt", "sub/plan.dxf", "C:evil"])
def test_unsafe_member_names_are_rejected(built: Built, tmp_path: Path, name: str) -> None:
    completed = run_verifier(rewrite(built.zip_bytes, extra={name: b"x"}), tmp_path)
    assert completed.returncode == 1
    assert f"- {name}: nombre de miembro inseguro" in completed.stdout
    assert "[--]   2." in completed.stdout  # nothing else is read


def test_duplicate_extra_and_missing_members_fail(built: Built, tmp_path: Path) -> None:
    duplicate = rewrite(built.zip_bytes, extra={"plan.dxf": b"0\nEOF\n"})
    completed = run_verifier(duplicate, tmp_path)
    assert completed.returncode == 1 and "- plan.dxf: miembro duplicado" in completed.stdout
    extra = rewrite(built.zip_bytes, extra={"notas.txt": b"x"})
    completed = run_verifier(extra, tmp_path)
    assert completed.returncode == 1
    assert "- notas.txt: archivo no listado en el manifiesto" in completed.stdout
    missing = rewrite(built.zip_bytes, {"plan.pdf": None})
    completed = run_verifier(missing, tmp_path)
    assert completed.returncode == 1 and "- plan.pdf: artefacto pdf_plan" in completed.stdout
    completed = run_verifier(b"no es un zip", tmp_path)
    assert completed.returncode == 1 and "- ZIP: no es un ZIP legible" in completed.stdout


def test_non_canonical_manifest_is_flagged(built: Built, tmp_path: Path) -> None:
    manifest = json.loads(members(built.zip_bytes)["manifest.json"])
    pretty = json.dumps(manifest, indent=1).encode()
    completed = run_verifier(rewrite(built.zip_bytes, {"manifest.json": pretty}), tmp_path)
    assert completed.returncode == 1 and "forma canónica" in completed.stdout
    assert "[OK]   3." in completed.stdout  # the signature covers the parsed content


def test_size_limit_guards_against_zip_bombs(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "MAX_TOTAL_BYTES", 1024)
    checks = verifier.verify_package(built.zip_bytes)
    assert checks[0].status == "fail" and "límite" in checks[0].failures[0][1]
    assert all(check.status == "skipped" for check in checks[1:])


def test_usage_errors_exit_2(built: Built, tmp_path: Path) -> None:
    missing = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(VERIFIER), str(tmp_path / "nope.zip")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 2 and "no existe" in missing.stderr
    assert run_verifier(built.zip_bytes, tmp_path, "--key-id", "abc").returncode == 2
    no_args = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(VERIFIER)], capture_output=True, text=True, check=False
    )
    assert no_args.returncode == 2


# --- Divergence guard: copied logic vs the application modules -----------------------------


def chain_variants() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    events = events_chain(8)
    yield "valid", events
    yield "empty", []
    for index in (0, 3, 7):
        tampered = [dict(event) for event in events]
        tampered[index]["after"] = {"operation": "forged"}
        yield f"payload-{index}", tampered
        rehashed = [dict(event) for event in tampered]
        rehashed[index]["hash"] = recompute_hash(rehashed[index])
        yield f"rehashed-{index}", rehashed
        broken_hash = [dict(event) for event in events]
        broken_hash[index]["hash"] = "0" * 64
        yield f"hash-{index}", broken_hash
    yield "missing-middle", events[:4] + events[5:]
    yield "missing-first", events[1:]
    yield "reordered", [events[0], events[2], events[1], *events[3:]]
    yield "other-case", [*events[:3], {**events[3], "case_file_id": "other"}]
    yield "malformed", [*events[:2], {**events[2], "extra": 1}]
    yield "bool-id", [{**events[0], "id": True}]
    yield "non-ascii-case", [{**events[0], "case_file_id": "ñ"}]
    yield "non-canonical", [*events[:1], {**events[1], "after": {"x": 2**60}}]
    auth: list[dict[str, Any]] = []
    prev = sha256_hex(b"auth")
    for index in range(3):
        event = {**events[index], "case_file_id": None, "prev_hash": prev}
        event["hash"] = recompute_hash(event)
        prev = event["hash"]
        auth.append(event)
    yield "auth-chain", auth
    yield "auth-mixed", [*auth[:2], events[2]]


@pytest.mark.parametrize(("label", "events"), list(chain_variants()))
def test_copied_verify_chain_matches_the_application(
    label: str, events: list[dict[str, Any]]
) -> None:
    expected = verify_chain(events)
    actual = verifier.verify_chain(events)
    assert (actual.valid, actual.checked, actual.first_invalid_id, actual.first_invalid_index) == (
        expected.valid,
        expected.checked,
        expected.first_invalid_id,
        expected.first_invalid_index,
    ), label
    assert (str(actual.reason), actual.detail) == (str(expected.reason), expected.detail), label


def test_copied_signature_check_matches_the_application(built: Built) -> None:
    manifest = json.loads(members(built.zip_bytes)["manifest.json"])
    other = Ed25519PrivateKey.generate().public_key()
    signature = manifest["signature"]
    variants: list[dict[str, Any]] = [
        manifest,
        {**manifest, "plan_id": "forged"},
        {name: value for name, value in manifest.items() if name != "signature"},
        {**manifest, "signature": {**signature, "scheme": "rsa"}},
        {**manifest, "signature": {**signature, "value": "@@"}},
        {**manifest, "signature": {**signature, "value": base64.b64encode(b"x").decode()}},
        {**manifest, "signature": {**signature, "value": 3}},
        {**manifest, "plan_id": float("nan")},
    ]
    for public_key in (built.key.public_key(), other):
        assert verifier.public_key_id(public_key) == public_key_id(public_key)
        for variant in variants:
            expected = verify_manifest(variant, public_key)
            actual = verifier.verify_manifest(variant, public_key)
            assert (actual.valid, str(actual.reason), actual.detail) == (
                expected.valid,
                str(expected.reason),
                expected.detail,
            )


# --- Dependencies: only the standard library, cryptography and rfc8785 --------------------


def test_script_imports_only_stdlib_cryptography_and_rfc8785() -> None:
    tree = ast.parse(VERIFIER.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module is not None
            roots.add(node.module.split(".")[0])
    assert "cryptography" in roots and "rfc8785" in roots
    assert roots - sys.stdlib_module_names <= ALLOWED_THIRD_PARTY


def _requirement_names(distribution: str) -> list[str]:
    names: list[str] = []
    for requirement in importlib.metadata.requires(distribution) or []:
        if "extra ==" in requirement:
            continue
        match = re.match(r"[A-Za-z0-9._-]+", requirement)
        if match is not None:
            names.append(match.group(0))
    return names


def restricted_site(target: Path) -> Path:
    """A directory with links to cryptography, rfc8785 and their runtime dependencies only."""
    target.mkdir()
    pending, seen = list(ALLOWED_THIRD_PARTY), set[str]()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue  # a dependency for another platform or Python version
        for file in distribution.files or []:
            top = file.parts[0]
            link = target / top
            if top != ".." and not link.exists():
                link.symlink_to(Path(str(distribution.locate_file(top))))
        pending.extend(_requirement_names(name))
    return target


def test_help_and_verification_run_with_only_cryptography_and_rfc8785(
    built: Built, tmp_path: Path
) -> None:
    site = restricted_site(tmp_path / "site")
    package = tmp_path / "paquete.zip"
    package.write_bytes(built.zip_bytes)

    def launch(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed interpreter and launcher
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                RESTRICTED_LAUNCHER,
                str(site),
                str(VERIFIER),
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=tmp_path,
            check=False,
        )

    help_run = launch("--help")
    assert help_run.returncode == 0, help_run.stderr
    assert "PAQUETE.zip" in help_run.stdout and "--public-key" in help_run.stdout
    verify_run = launch(str(package))
    assert verify_run.returncode == 0, verify_run.stdout + verify_run.stderr
    assert "RESULTADO: OK" in verify_run.stdout
