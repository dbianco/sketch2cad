"""Manifest (spec 14.2), ed25519-jcs-v1 signature and signing key handling (REQ-14, H4-11).

Keys are generated at runtime: in memory from seed bytes built here, or as files under
``tmp_path``. No key material is stored in the repository.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sketch2cad.api import create_server_app
from sketch2cad.cli import run
from sketch2cad.package.manifest import (
    MANIFEST_KEYS,
    Actor,
    ArtifactEntry,
    SoftwareInfo,
    build_manifest,
    canonical_manifest_bytes,
    sha256_hex,
)
from sketch2cad.package.signing import (
    SCHEME,
    InvalidReason,
    SigningKeyError,
    encode_public_key,
    generate_signing_key,
    load_public_key,
    load_signing_key,
    public_key_id,
    public_key_pem,
    require_signing_key,
    sign_manifest,
    signing_payload,
    verify_manifest,
)

API_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = API_DIR.parent
KEY_MATERIAL = re.compile(rb"BEGIN (PRIVATE|OPENSSH)")
# Paths allow-listed in .gitleaks.toml: deliberately fake keys proving the scanner works.
FAKE_FIXTURE_DIRS = ("sketch2cad-api/tests/fixtures/", "sketch2cad-web/tests/fixtures/")
FAKE_MARKER = b"FAKE TEST FIXTURE - NOT A REAL KEY"
UNSAFE_MODES = (0o644, 0o640, 0o604, 0o660, 0o606, 0o700)


def seed_key(seed: int = 7) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes((seed + i) % 256 for i in range(32)))


def sample_manifest() -> dict[str, Any]:
    reviewer = Actor(
        user_id="8f1c0c8e-4a52-4c1e-9d55-7d1f0f6b2a10", display_name="Ana", role="reviewer"
    )
    return build_manifest(
        case_file_id="2b7e1516-28ae-4d2a-a6d2-15e1f2c4a001",
        case_file_code="EXP-2026-001",
        plan_id="5d6c9f3e-1b2a-4c3d-8e9f-0a1b2c3d4e5f",
        revision_number=3,
        snapshot_sha256=sha256_hex(b"snapshot"),
        artifacts=[
            ArtifactEntry("source_original", "croquis.jpg", sha256_hex(b"original")),
            ArtifactEntry("dxf", "plan.dxf", sha256_hex(b"dxf")),
            ArtifactEntry("pdf_plan", "plan.pdf", sha256_hex(b"pdf")),
            ArtifactEntry("pdf_audit", "audit.pdf", sha256_hex(b"audit")),
        ],
        events_count=214,
        events_last_hash=sha256_hex(b"last event"),
        calibration={"mode": "homography", "reprojection_error_mm": 0.4, "paper_scale": 100},
        local_frame={"origin": [0.0, 0.0], "rotation_deg": 12.5},
        metric_status="metric",
        software=SoftwareInfo("0.1.0", "abc1234", "pipeline-1", None),
        catalog_version="default-1",
        validated_by=reviewer,
        validated_at=datetime(2026, 10, 24, 15, 4, 5, tzinfo=UTC),
        exported_by=reviewer,
        exported_at=datetime(2026, 10, 24, 10, 30, tzinfo=timezone(timedelta(hours=-5))),
        timezone_display="America/Panama",
    )


def leaf_paths(value: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if isinstance(value, dict):
        items = cast(dict[str, Any], value)
        return [path for key, item in items.items() for path in leaf_paths(item, (*prefix, key))]
    if isinstance(value, list):
        elements = cast(list[Any], value)
        return [path for i, item in enumerate(elements) for path in leaf_paths(item, (*prefix, i))]
    return [prefix]


def mutated(manifest: dict[str, Any], path: tuple[Any, ...]) -> dict[str, Any]:
    copy: dict[str, Any] = json.loads(json.dumps(manifest))
    parent: Any = copy
    for key in path[:-1]:
        parent = parent[key]
    old = parent[path[-1]]
    if isinstance(old, str) and old:
        parent[path[-1]] = old[:-1] + ("0" if old[-1] != "0" else "1")
    elif isinstance(old, bool) or old is None:
        parent[path[-1]] = "x"
    elif isinstance(old, int | float):
        parent[path[-1]] = old + 1
    else:
        parent[path[-1]] = "x"
    return copy


# --- Manifest ------------------------------------------------------------------------------


def test_manifest_has_exactly_the_spec_keys() -> None:
    manifest = sample_manifest()

    assert tuple(manifest) == MANIFEST_KEYS
    assert manifest["manifest_version"] == "2.0"
    assert manifest["revision"]["number"] == 3
    assert manifest["events_chain"] == {"count": 214, "last_hash": sha256_hex(b"last event")}
    assert manifest["validated_at"] == "2026-10-24T15:04:05Z"
    assert manifest["exported_at"] == "2026-10-24T15:30:00Z"
    assert [entry["kind"] for entry in manifest["artifacts"]][1:] == [
        "dxf",
        "pdf_plan",
        "pdf_audit",
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"snapshot_sha256": "ABC"}, "snapshot_sha256"),
        ({"events_last_hash": "0" * 63}, "events_last_hash"),
        ({"metric_status": "exact"}, "metric_status"),
        ({"validated_at": datetime(2026, 10, 24)}, "timezone-aware"),
        ({"revision_number": 0}, "revision_number"),
        ({"artifacts": [ArtifactEntry("dxf", "../plan.dxf", "0" * 64)]}, "plain file name"),
        ({"artifacts": [ArtifactEntry("dxf", "a", "0" * 64)] * 2}, "unique"),
        ({"artifacts": [ArtifactEntry("dxf", "a", "0" * 63)]}, "artifacts[0].sha256"),
    ],
)
def test_build_manifest_rejects_malformed_input(change: dict[str, Any], message: str) -> None:
    arguments: dict[str, Any] = {
        "case_file_id": "c",
        "case_file_code": "code",
        "plan_id": "p",
        "revision_number": 1,
        "snapshot_sha256": "0" * 64,
        "artifacts": [],
        "events_count": 1,
        "events_last_hash": "0" * 64,
        "calibration": None,
        "local_frame": None,
        "metric_status": "approximate",
        "software": SoftwareInfo("v", "g", "p", None),
        "catalog_version": "default-1",
        "validated_by": Actor("u", None, "reviewer"),
        "validated_at": datetime(2026, 10, 24, tzinfo=UTC),
        "exported_by": Actor("u", None, "reviewer"),
        "exported_at": datetime(2026, 10, 24, tzinfo=UTC),
        "timezone_display": "America/Panama",
        **change,
    }
    with pytest.raises(ValueError, match=re.escape(message)):
        build_manifest(**arguments)


def test_canonicalization_is_independent_of_key_order() -> None:
    manifest = sample_manifest()
    reordered = {key: manifest[key] for key in reversed(list(manifest))}
    reordered["revision"] = dict(reversed(list(manifest["revision"].items())))

    assert canonical_manifest_bytes(reordered) == canonical_manifest_bytes(manifest)
    assert signing_payload(reordered) == signing_payload(manifest)
    assert verify_manifest(sign_manifest(reordered, seed_key()), seed_key().public_key()).valid
    signed = sign_manifest(manifest, seed_key())
    shuffled = {key: signed[key] for key in sorted(signed, reverse=True)}
    assert verify_manifest(shuffled, seed_key().public_key()).valid


# --- Signature -----------------------------------------------------------------------------


def test_sign_and_verify_the_sample_manifest() -> None:
    key = seed_key()
    signed = sign_manifest(sample_manifest(), key)

    assert set(signed["signature"]) == {"scheme", "public_key_id", "value"}
    assert signed["signature"]["scheme"] == SCHEME == "ed25519-jcs-v1"
    assert signed["signature"]["public_key_id"] == public_key_id(key.public_key())
    assert signing_payload(signed) == rfc8785.dumps(sample_manifest())
    assert verify_manifest(signed, key.public_key()).valid
    roundtrip = json.loads(canonical_manifest_bytes(signed))
    assert verify_manifest(roundtrip, key.public_key()).valid


def test_signatures_are_deterministic() -> None:
    first = sign_manifest(sample_manifest(), seed_key())
    second = sign_manifest(sample_manifest(), seed_key())

    assert first["signature"]["value"] == second["signature"]["value"]
    assert sign_manifest(first, seed_key()) == first


def test_changing_any_field_breaks_the_signature() -> None:
    key = seed_key()
    signed = sign_manifest(sample_manifest(), key)
    paths = [path for path in leaf_paths(signed) if path[0] != "signature"]

    assert len(paths) > 30
    for path in paths:
        result = verify_manifest(mutated(signed, path), key.public_key())
        assert not result.valid, path
        assert result.reason is InvalidReason.BAD_SIGNATURE, path


def test_changing_any_byte_of_manifest_json_breaks_verification() -> None:
    key = seed_key()
    data = canonical_manifest_bytes(sign_manifest(sample_manifest(), key))

    for position in range(len(data)):
        tampered = bytearray(data)
        tampered[position] ^= 0x01
        try:
            manifest = json.loads(bytes(tampered))
        except ValueError:
            continue  # no longer JSON: rejected before the signature check
        if not isinstance(manifest, dict):
            continue
        parsed = cast(dict[str, Any], manifest)
        assert not verify_manifest(parsed, key.public_key()).valid, position


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("scheme", "ed25519-jcs-v2", InvalidReason.UNKNOWN_SCHEME),
        ("public_key_id", "ed25519:0000000000000000", InvalidReason.KEY_ID_MISMATCH),
        ("value", "not base64!", InvalidReason.MALFORMED_SIGNATURE),
        ("value", "AAAA", InvalidReason.MALFORMED_SIGNATURE),
    ],
)
def test_tampered_signature_fields_fail(field: str, value: str, reason: InvalidReason) -> None:
    key = seed_key()
    signed = sign_manifest(sample_manifest(), key)
    signed["signature"][field] = value

    result = verify_manifest(signed, key.public_key())

    assert (result.valid, result.reason) == (False, reason)


def test_signature_value_changed_or_missing_or_wrong_key_fails() -> None:
    key = seed_key()
    signed = sign_manifest(sample_manifest(), key)
    other = sign_manifest(mutated(sample_manifest(), ("plan_id",)), key)
    signed_with_other_value = {**signed, "signature": other["signature"]}
    unsigned = {k: v for k, v in signed.items() if k != "signature"}

    bad = verify_manifest(signed_with_other_value, key.public_key())
    assert (bad.valid, bad.reason) == (False, InvalidReason.BAD_SIGNATURE)
    missing = verify_manifest(unsigned, key.public_key())
    assert missing.reason is InvalidReason.MISSING_SIGNATURE
    wrong_key = verify_manifest(signed, seed_key(99).public_key())
    assert wrong_key.reason is InvalidReason.KEY_ID_MISMATCH
    # A different public key relabelled with the expected id still fails cryptographically.
    forged = {**signed, "signature": {**signed["signature"]}}
    forged["signature"]["public_key_id"] = public_key_id(seed_key(99).public_key())
    assert verify_manifest(forged, seed_key(99).public_key()).reason is InvalidReason.BAD_SIGNATURE


def test_public_key_encodings_roundtrip() -> None:
    public_key = seed_key().public_key()

    assert re.fullmatch(r"ed25519:[0-9a-f]{16}", public_key_id(public_key))
    for encoded in (public_key_pem(public_key), encode_public_key(public_key)):
        assert public_key_id(load_public_key(encoded)) == public_key_id(public_key)
    with pytest.raises(ValueError):
        load_public_key("AAAA")


# --- Key file ------------------------------------------------------------------------------


def test_generate_creates_a_0600_file_in_new_directories(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "nested" / "signing-key.pem"

    key = generate_signing_key(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0
    assert sorted(p.name for p in path.parent.iterdir()) == ["signing-key.pem"]
    loaded = load_signing_key(path)
    assert public_key_id(loaded.public_key()) == public_key_id(key.public_key())


def test_generate_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "signing-key.pem"
    generate_signing_key(path)
    before = path.read_bytes()

    with pytest.raises(SigningKeyError, match=re.escape(str(path))):
        generate_signing_key(path)
    assert path.read_bytes() == before


def test_loaded_key_signs_deterministically(tmp_path: Path) -> None:
    path = tmp_path / "signing-key.pem"
    generate_signing_key(path)

    first = sign_manifest(sample_manifest(), load_signing_key(path))
    second = sign_manifest(sample_manifest(), load_signing_key(path))

    assert first == second
    assert verify_manifest(first, load_signing_key(path).public_key()).valid


@pytest.mark.parametrize("mode", UNSAFE_MODES)
def test_key_file_with_group_or_other_bits_is_refused(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "signing-key.pem"
    generate_signing_key(path)
    path.chmod(mode)

    with pytest.raises(SigningKeyError, match=re.escape(f"{path} has mode {mode:04o}")):
        load_signing_key(path)
    with pytest.raises(SystemExit) as excinfo:
        require_signing_key({"SIGNING_KEY_PATH": str(path)})
    assert str(path) in str(excinfo.value.code)


def test_stricter_mode_0400_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "signing-key.pem"
    generate_signing_key(path)
    path.chmod(0o400)

    assert isinstance(require_signing_key({"SIGNING_KEY_PATH": str(path)}), Ed25519PrivateKey)


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _missing(path: Path) -> None:
    del path


def _directory(path: Path) -> None:
    path.mkdir(mode=0o700)


def _garbage(path: Path) -> None:
    _write_private(path, b"not a key")


def _ec_key(path: Path) -> None:
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_private(path, pem)


@pytest.mark.parametrize(
    ("prepare", "message"),
    [
        (_missing, "does not exist"),
        (_directory, "is not a regular file"),
        (_garbage, "not an unencrypted PEM"),
        (_ec_key, "does not hold an Ed25519 key"),
    ],
)
def test_unusable_key_files_name_the_path(
    tmp_path: Path, prepare: Callable[[Path], None], message: str
) -> None:
    path = tmp_path / "signing-key.pem"
    prepare(path)

    with pytest.raises(SystemExit) as excinfo:
        require_signing_key({"SIGNING_KEY_PATH": str(path)})
    text = str(excinfo.value.code)
    assert str(path) in text and message in text and "refusing to start" in text


def test_unset_signing_key_path_refuses_to_start() -> None:
    with pytest.raises(SystemExit, match="SIGNING_KEY_PATH is not set"):
        require_signing_key({})


def test_api_server_factory_checks_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "signing-key.pem"
    generate_signing_key(path)
    monkeypatch.setenv("SIGNING_KEY_PATH", str(path))
    assert create_server_app().title == "Sketch2CAD API"

    path.chmod(0o644)
    with pytest.raises(SystemExit) as excinfo:
        create_server_app()
    assert str(path) in str(excinfo.value.code)


def _run_entry(args: list[str], key_path: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SIGNING_KEY_PATH": str(key_path)}
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        args, capture_output=True, text=True, cwd=API_DIR, env=env, timeout=60, check=False
    )


def test_uvicorn_and_worker_entries_exit_non_zero_naming_the_path(tmp_path: Path) -> None:
    path = tmp_path / "signing-key.pem"
    generate_signing_key(path)
    path.chmod(0o644)
    procrastinate_bin = str(Path(sys.executable).with_name("procrastinate"))
    entries = [
        [sys.executable, "-m", "uvicorn", "--factory", "sketch2cad.api:create_server_app"],
        [procrastinate_bin, "--app=sketch2cad.worker.app.worker_app", "healthchecks"],
    ]

    for args in entries:
        result = _run_entry(args, path)
        assert result.returncode != 0, args
        assert f"signing key file {path} has mode 0644" in result.stderr, result.stderr
        assert "Traceback" not in result.stderr


def test_worker_app_loads_with_a_valid_key_and_app_needs_none(tmp_path: Path) -> None:
    path = tmp_path / "signing-key.pem"
    generate_signing_key(path)
    script = (
        "import os, sketch2cad.worker.app as m\n"
        "assert m.worker_app is m.app\n"
        "os.environ['SIGNING_KEY_PATH'] = os.environ['SIGNING_KEY_PATH'] + '.missing'\n"
        "m.app  # the API defers jobs through app: no key check there\n"
    )
    env_url = "postgresql+psycopg://user:pw@127.0.0.1:1/unused"

    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=API_DIR,
        env={**os.environ, "SIGNING_KEY_PATH": str(path), "DATABASE_URL": env_url},
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr


# --- CLI -----------------------------------------------------------------------------------


def test_cli_generates_a_key_and_never_prints_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "keys" / "signing-key.pem"

    assert run(["generate-signing-key", "--path", str(path)], sys.stdin) == 0
    output = capsys.readouterr().out
    key = load_signing_key(path)
    assert f"public_key_id={public_key_id(key.public_key())}" in output
    assert "PRIVATE" not in output
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    assert run(["generate-signing-key", "--path", str(path)], sys.stdin) == 1
    assert "already exists" in capsys.readouterr().err
    assert run(["generate-signing-key", "--path", str(path), "--if-missing"], sys.stdin) == 0
    assert "kept existing" in capsys.readouterr().out
    assert public_key_id(load_signing_key(path).public_key()) == public_key_id(key.public_key())


def test_cli_if_missing_refuses_an_unsafe_existing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "signing-key.pem"
    generate_signing_key(path)
    path.chmod(0o644)
    monkeypatch.setenv("SIGNING_KEY_PATH", str(path))

    assert run(["generate-signing-key", "--if-missing"], sys.stdin) == 1
    assert f"{path} has mode 0644" in capsys.readouterr().err
    monkeypatch.delenv("SIGNING_KEY_PATH")
    assert run(["generate-signing-key"], sys.stdin) == 1
    assert "pass --path or set SIGNING_KEY_PATH" in capsys.readouterr().err


def test_cli_module_entry_point(tmp_path: Path) -> None:
    path = tmp_path / "signing-key.pem"
    command = [sys.executable, "-m", "sketch2cad.cli", "generate-signing-key", "--path", str(path)]

    result = _run_entry(command, path)

    assert result.returncode == 0, result.stderr
    assert "public_key_id=ed25519:" in result.stdout
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --- Repository ----------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_repository_holds_no_private_key_material() -> None:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        capture_output=True,
        cwd=REPO_ROOT,
        check=True,
    ).stdout
    findings: list[str] = []
    for name in sorted({item.decode() for item in listed.split(b"\0") if item}):
        path = REPO_ROOT / name
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if not KEY_MATERIAL.search(data):
            continue
        if name.startswith(FAKE_FIXTURE_DIRS) and FAKE_MARKER in data:
            continue  # gitleaks self-test fixture, allow-listed in .gitleaks.toml
        findings.append(name)

    assert findings == []
