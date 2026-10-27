"""License allow-list check (DEC-6) driven by synthetic pip-licenses JSON; no database needed."""

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

API_DIR = Path(__file__).resolve().parents[1]
SCRIPT = API_DIR / "scripts" / "check_licenses.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_licenses", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_licenses"] = module
    spec.loader.exec_module(module)
    return module


_script = _load_script()
normalize_license = cast(Callable[[str], str], _script.normalize_license)
classify = cast(Callable[[str, frozenset[str]], str], _script.classify)
parse_exceptions = cast(
    Callable[[dict[str, Any]], tuple[list[Any], list[str]]], _script.parse_exceptions
)
parse_packages = cast(Callable[..., list[Any]], _script.parse_packages)
evaluate = cast(Callable[..., Any], _script.evaluate)
load_allowed = cast(Callable[[Path], frozenset[str]], _script.load_allowed)
main = cast(Callable[[list[str]], int], _script.main)

ALLOWED = load_allowed(API_DIR / "licenses-allowed.txt")
PSYCOPG_EXCEPTION = {
    "package": "psycopg",
    "version": "*",
    "license": "LGPL-3.0-only",
    "reason": "Required by Procrastinate.",
    "status": "approved",
}


def _payload(*rows: tuple[str, str, str]) -> str:
    return json.dumps([{"Name": n, "Version": v, "License": lic} for n, v, lic in rows])


def _run(rows: list[tuple[str, str, str]], exceptions: list[dict[str, str]]) -> Any:
    entries, errors = parse_exceptions({"exception": exceptions})
    return evaluate(parse_packages(_payload(*rows)), ALLOWED, entries, errors)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MIT License", "MIT"),
        ("BSD License", "BSD"),
        ("BSD-3-Clause", "BSD-3-Clause"),
        ("Apache Software License", "Apache-2.0"),
        ("Apache-2.0", "Apache-2.0"),
        ("Python Software Foundation License", "PSF-2.0"),
        ("PSF-2.0", "PSF-2.0"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "LGPL-3.0"),
        ("GNU General Public License v3 (GPLv3)", "GPL-3.0"),
        ("Apache Software License; BSD License", "Apache-2.0 AND BSD"),
        ("Apache-2.0 OR BSD-2-Clause", "Apache-2.0 OR BSD-2-Clause"),
        ("(MIT OR Apache-2.0) AND BSD-3-Clause", "(MIT OR Apache-2.0) AND BSD-3-Clause"),
        ("", "UNKNOWN"),
    ],
)
def test_normalization(raw: str, expected: str) -> None:
    assert normalize_license(raw) == expected


def test_allowed_file_has_the_dec6_families_and_no_copyleft() -> None:
    assert {"MIT", "BSD", "BSD-3-Clause", "Apache-2.0", "PSF-2.0"} <= ALLOWED
    assert not any("GPL" in item for item in ALLOWED)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MIT", "allowed"),
        ("Apache-2.0 OR BSD-2-Clause", "allowed"),
        ("Apache Software License; BSD License", "allowed"),
        ("MIT AND EUPL-1.2", "needs-exception"),
        ("GPL-3.0-only OR MIT", "allowed"),
        ("MIT AND GPL-3.0-only", "forbidden"),
        ("LGPL-3.0-only", "needs-exception"),
        ("EUPL-1.2", "needs-exception"),
        ("UNKNOWN", "needs-exception"),
        ("GNU Affero General Public License v3", "forbidden"),
        ("AGPL-3.0-or-later", "forbidden"),
        ("GNU General Public License v2 or later (GPLv2+)", "forbidden"),
    ],
)
def test_classification(raw: str, expected: str) -> None:
    assert classify(raw, ALLOWED) == expected


def test_allow_listed_packages_pass_without_exceptions() -> None:
    report = _run([("fastapi", "1.0", "MIT"), ("typing_extensions", "4.0", "PSF-2.0")], [])
    assert report.errors == [] and report.warnings == []


def test_lgpl_package_needs_an_exception() -> None:
    report = _run([("psycopg", "3.3.6", "LGPL-3.0-only")], [])
    assert report.errors == [
        "psycopg 3.3.6: LGPL-3.0-only (not in the allow-list and no matching exception)"
    ]


def test_lgpl_package_passes_with_a_documented_exception() -> None:
    report = _run([("psycopg", "3.3.6", "LGPL-3.0-only")], [PSYCOPG_EXCEPTION])
    assert report.errors == []


def test_exception_with_another_version_or_license_does_not_match() -> None:
    pinned = {**PSYCOPG_EXCEPTION, "version": "3.2.0"}
    relicensed = {**PSYCOPG_EXCEPTION, "license": "MPL-2.0"}
    for exception in (pinned, relicensed):
        report = _run([("psycopg", "3.3.6", "LGPL-3.0-only")], [exception])
        assert any("no matching exception" in error for error in report.errors)
        assert any(error.startswith("stale exception: psycopg") for error in report.errors)


def test_exception_matches_normalized_package_names() -> None:
    exception = {**PSYCOPG_EXCEPTION, "package": "Psycopg_Binary"}
    report = _run([("psycopg-binary", "3.3.6", "LGPL-3.0-only")], [exception])
    assert report.errors == []


def test_stale_exception_fails() -> None:
    report = _run([("fastapi", "1.0", "MIT")], [PSYCOPG_EXCEPTION])
    assert report.errors == [
        "stale exception: psycopg * (LGPL-3.0-only) matches no installed package that needs it"
    ]


def test_exception_without_reason_fails() -> None:
    exception = {**PSYCOPG_EXCEPTION, "reason": "   "}
    report = _run([("psycopg", "3.3.6", "LGPL-3.0-only")], [exception])
    assert "exception #1 (psycopg): missing or empty 'reason'" in report.errors
    assert any("no matching exception" in error for error in report.errors)


def test_exception_with_unknown_status_fails() -> None:
    _, errors = parse_exceptions({"exception": [{**PSYCOPG_EXCEPTION, "status": "ok"}]})
    assert errors == [
        "exception #1 (psycopg): status must be one of approved, pending-owner-decision"
    ]


def test_pending_exception_passes_with_a_warning() -> None:
    pending = {
        "package": "somepkg",
        "version": "*",
        "license": "EUPL-1.2",
        "reason": "Owner to decide.",
        "status": "pending-owner-decision",
    }
    report = _run([("somepkg", "2.1.1", "EUPL-1.2")], [pending])
    assert report.errors == []
    assert report.warnings == ["somepkg 2.1.1: EUPL-1.2 (exception pending-owner-decision)"]


def test_agpl_is_denied_even_with_an_exception() -> None:
    rows = [("minio", "7.0", "GNU Affero General Public License v3")]
    without_reason = {"package": "minio", "version": "*", "license": rows[0][2]}
    report = _run(rows, [{**without_reason, "status": "approved", "reason": ""}])
    assert "exception #1 (minio): missing or empty 'reason'" in report.errors
    assert any(error.startswith("minio 7.0: GNU Affero") for error in report.errors)

    with_reason = {**without_reason, "status": "approved", "reason": "Someone wants it."}
    report = _run(rows, [with_reason])
    assert report.errors == [
        "minio 7.0: GNU Affero General Public License v3 "
        "(GPL/AGPL is forbidden; exceptions cannot allow it)"
    ]


def test_parse_packages_skips_names() -> None:
    payload = _payload(("sketch2cad", "0.1.0", "UNKNOWN"), ("fastapi", "1.0", "MIT"))
    names = [p.name for p in parse_packages(payload, skip=frozenset({"sketch2cad"}))]
    assert names == ["fastapi"]


def _write_inputs(tmp_path: Path, rows: list[tuple[str, str, str]]) -> tuple[Path, Path]:
    payload = tmp_path / "licenses.json"
    payload.write_text(_payload(*rows), encoding="utf-8")
    exceptions = tmp_path / "exceptions.toml"
    exceptions.write_text(
        '[[exception]]\npackage = "psycopg"\nversion = "*"\nlicense = "LGPL-3.0-only"\n'
        'status = "approved"\nreason = "Required by Procrastinate."\n',
        encoding="utf-8",
    )
    return payload, exceptions


def test_gpl_package_fails_main_and_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        ("psycopg", "3.3.6", "LGPL-3.0-only"),
        ("fastapi", "1.0", "MIT"),
        ("gpl-thing", "1.2.3", "GPL-3.0-only"),
    ]
    payload, exceptions = _write_inputs(tmp_path, rows)

    assert main(["--input", str(payload), "--exceptions", str(exceptions)]) == 1
    output = capsys.readouterr().out
    assert "gpl-thing 1.2.3: GPL-3.0-only" in output
    assert "psycopg" not in output.split("failed", 1)[1]


def test_gpl_package_fails_in_a_subprocess(tmp_path: Path) -> None:
    rows = [("psycopg", "3.3.6", "LGPL-3.0-only"), ("gpl-thing", "1.2.3", "GPL-3.0-only")]
    payload, exceptions = _write_inputs(tmp_path, rows)
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--input", str(payload), "--exceptions", str(exceptions)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "gpl-thing 1.2.3: GPL-3.0-only" in result.stdout


def test_clean_input_passes_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload, exceptions = _write_inputs(
        tmp_path, [("psycopg", "3.3.6", "LGPL-3.0-only"), ("fastapi", "1.0", "MIT")]
    )
    assert main(["--input", str(payload), "--exceptions", str(exceptions)]) == 0
    assert "License check passed" in capsys.readouterr().out


def test_committed_exceptions_document_psycopg() -> None:
    entries, errors = _script.parse_exceptions(
        _script.tomllib.loads((API_DIR / "license-exceptions.toml").read_text(encoding="utf-8"))
    )
    assert errors == []
    psycopg = [e for e in entries if e.package == "psycopg"]
    assert len(psycopg) == 1 and psycopg[0].status == "approved" and psycopg[0].reason


def test_installed_environment_passes_the_license_check() -> None:
    """CA-13 on the real environment: pip-licenses over the installed packages, no network."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "License check passed" in result.stdout
