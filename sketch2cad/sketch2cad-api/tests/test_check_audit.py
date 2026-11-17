"""pip-audit severity gate (H0-8) driven by synthetic pip-audit JSON; no database needed."""

import importlib.util
import json
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

API_DIR = Path(__file__).resolve().parents[1]
SCRIPT = API_DIR / "scripts" / "check_audit.py"
TODAY = "2026-10-25"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_audit"] = module
    spec.loader.exec_module(module)
    return module


_script = _load_script()
parse_severity = cast(Callable[[object], str], _script.parse_severity)
main = cast(Callable[[list[str]], int], _script.main)


def _vuln(vuln_id: str = "PYSEC-2099-1", severity: object = None, **extra: Any) -> dict[str, Any]:
    vuln: dict[str, Any] = {
        "id": vuln_id,
        "fix_versions": ["2.0.1"],
        "aliases": ["CVE-2099-0001", "GHSA-aaaa-bbbb-cccc"],
        "description": "Synthetic vulnerability.",
        **extra,
    }
    if severity is not None:
        vuln["severity"] = severity
    return vuln


def _audit(*vulns: dict[str, Any], package: str = "vulnpkg") -> dict[str, Any]:
    return {
        "dependencies": [
            {"name": package, "version": "2.0.0", "vulns": list(vulns)},
            {"name": "safepkg", "version": "1.0.0", "vulns": []},
            {"name": "sketch2cad", "skip_reason": "Dependency not found on PyPI"},
        ],
        "fixes": [],
    }


def _exception(**overrides: str) -> str:
    fields = {
        "id": "PYSEC-2099-1",
        "package": "vulnpkg",
        "justification": "Vulnerable code path is not reachable from the API.",
        "expires": "2026-12-31",
        "status": "accepted-risk",
        **overrides,
    }
    body = "\n".join(
        f"{key} = {value}" if key == "expires" else f"{key} = {json.dumps(value)}"
        for key, value in fields.items()
    )
    return f"[[exception]]\n{body}\n"


def _check(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    audit: dict[str, Any] | str,
    exceptions: str = "exception = []\n",
    today: str = TODAY,
) -> tuple[int, str]:
    audit_file = tmp_path / "audit.json"
    audit_file.write_text(audit if isinstance(audit, str) else json.dumps(audit), "utf-8")
    exceptions_file = tmp_path / "audit-exceptions.toml"
    exceptions_file.write_text(exceptions, encoding="utf-8")
    argv = ["--input", str(audit_file), "--exceptions", str(exceptions_file), "--today", today]
    code = main(argv)
    return code, capsys.readouterr().out


def test_high_finding_without_exception_fails_naming_package_and_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="HIGH")))
    assert code == 1
    assert "vulnpkg 2.0.0: PYSEC-2099-1" in out
    assert "no valid exception" in out


def test_same_high_finding_with_justified_exception_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="HIGH")), _exception())
    assert code == 0, out
    assert "excepted: accepted-risk" in out
    assert "Audit check passed" in out


def test_exception_matching_an_alias_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exceptions = _exception(id="GHSA-aaaa-bbbb-cccc")
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="CRITICAL")), exceptions)
    assert code == 0, out


def test_exception_for_another_package_does_not_cover_the_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exceptions = _exception(package="otherpkg")
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="HIGH")), exceptions)
    assert code == 1
    assert "no valid exception" in out
    assert "stale exception: otherpkg PYSEC-2099-1" in out


def test_expired_exception_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exceptions = _exception(expires="2026-10-24")
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="HIGH")), exceptions)
    assert code == 1
    assert "expired exception: vulnpkg PYSEC-2099-1 expired on 2026-10-24" in out
    assert "no valid exception" in out


def test_exception_expiring_today_is_still_valid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exceptions = _exception(expires=TODAY)
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="HIGH")), exceptions)
    assert code == 0, out


def test_stale_exception_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _check(tmp_path, capsys, _audit(), _exception())
    assert code == 1
    assert "stale exception: vulnpkg PYSEC-2099-1" in out


@pytest.mark.parametrize(
    ("override", "problem"),
    [
        ({"justification": "  "}, "missing or empty 'justification'"),
        ({"status": ""}, "missing or empty 'status'"),
        ({"status": "approved"}, "status must be one of"),
        ({"expires": '"soon"'}, "invalid 'expires' date"),
    ],
)
def test_invalid_exception_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    override: dict[str, str],
    problem: str,
) -> None:
    exceptions = _exception(**override)
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="HIGH")), exceptions)
    assert code == 1
    assert problem in out
    assert "no valid exception" in out


def test_exception_without_expires_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exceptions = (
        '[[exception]]\nid = "PYSEC-2099-1"\npackage = "vulnpkg"\n'
        'justification = "Not reachable."\nstatus = "accepted-risk"\n'
    )
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="HIGH")), exceptions)
    assert code == 1
    assert "missing or invalid 'expires' date" in out


def test_finding_without_severity_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _check(tmp_path, capsys, _audit(_vuln()))
    assert code == 1
    assert "severity UNKNOWN" in out
    assert "PYSEC-2099-1" in out


def test_low_and_medium_findings_only_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    audit = _audit(_vuln("PYSEC-2099-2", "LOW"), _vuln("PYSEC-2099-3", "moderate"))
    code, out = _check(tmp_path, capsys, audit)
    assert code == 0, out
    assert "WARNING vulnpkg 2.0.0: PYSEC-2099-2" in out
    assert "severity MEDIUM" in out
    assert "SKIPPED sketch2cad" in out


def test_repeated_finding_is_reported_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _check(tmp_path, capsys, _audit(_vuln(severity="HIGH"), _vuln(severity="HIGH")))
    assert code == 1
    assert out.count("vulnpkg 2.0.0: PYSEC-2099-1") == 1
    assert "1 problems" in out


def test_clean_audit_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _check(tmp_path, capsys, _audit())
    assert code == 0, out
    assert "Checked 3 dependencies, 0 findings" in out


@pytest.mark.parametrize("payload", ["", "not json", '{"fixes": []}', "[]"])
def test_non_audit_input_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: str
) -> None:
    code, out = _check(tmp_path, capsys, payload)
    assert code == 1
    assert "Audit check failed" in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HIGH", "HIGH"),
        ("critical", "CRITICAL"),
        ("Moderate", "MEDIUM"),
        ("low", "LOW"),
        (9.8, "CRITICAL"),
        (7.0, "HIGH"),
        ("6.9", "MEDIUM"),
        (3.1, "LOW"),
        ([{"type": "CVSS_V3", "score": "7.5"}], "HIGH"),
        ([{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}], "UNKNOWN"),
        ({"severity": "LOW", "score": 9.1}, "CRITICAL"),
        (None, "UNKNOWN"),
        ("", "UNKNOWN"),
        ([], "UNKNOWN"),
        (True, "UNKNOWN"),
    ],
)
def test_parse_severity(raw: object, expected: str) -> None:
    assert parse_severity(raw) == expected


def test_repository_exceptions_file_is_valid() -> None:
    with (API_DIR / "audit-exceptions.toml").open("rb") as handle:
        data = tomllib.load(handle)
    entries, errors = cast(tuple[list[Any], list[str]], _script.parse_exceptions(data))
    assert errors == []
    assert isinstance(entries, list)


def test_script_reads_pip_audit_json_from_stdin() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        input=json.dumps(_audit(_vuln(severity="HIGH"))),
        capture_output=True,
        text=True,
        check=False,
        cwd=API_DIR,
    )
    assert result.returncode == 1
    assert "vulnpkg 2.0.0: PYSEC-2099-1" in result.stdout
