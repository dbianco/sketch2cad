"""Fail on high-severity pip-audit findings that are not excepted (jotdown H0-8).

Usage (from sketch2cad-api/)::

    uv run pip-audit --format json | uv run python scripts/check_audit.py
    uv run python scripts/check_audit.py --input audit.json

Severity decision (fail closed): the JSON written by ``pip-audit --format json`` lists, per
dependency, ``vulns`` entries with ``id``, ``aliases``, ``fix_versions`` and ``description``;
it does not always carry a severity. This script reads an optional ``severity`` field when
present (a label such as ``"HIGH"``/``"moderate"``, a numeric CVSS base score, or a list or
table of those, e.g. OSV-style ``[{"type": "CVSS_V3", "score": "7.5"}]``) and maps CVSS scores
with the standard CVSS v3 bands (>= 9.0 critical, >= 7.0 high, >= 4.0 medium, else low).
When no severity can be read (absent field, CVSS vector string, unknown label) the finding is
UNKNOWN and is treated as high. Therefore every finding without a readable severity blocks
the build until it is fixed or excepted.

Rules:

* HIGH, CRITICAL and UNKNOWN findings fail unless an exception in ``audit-exceptions.toml``
  matches them: same package and an ``id`` equal to the vulnerability id or one of its aliases.
* LOW and MEDIUM findings only print a warning.
* Exceptions need non-empty ``id``, ``package``, ``justification``, ``status`` (one of
  ``STATUSES``) and an ``expires`` date. Expired exceptions fail (and no longer cover their
  finding); exceptions that match no current finding are stale and fail.
* Input that is not pip-audit JSON (for example empty output after a pip-audit crash) fails.
"""

import argparse
import datetime as dt
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

API_DIR = Path(__file__).resolve().parents[1]
EXCEPTIONS_PATH = API_DIR / "audit-exceptions.toml"

STATUSES = ("accepted-risk", "pending-owner-decision")
BLOCKING = frozenset({"HIGH", "CRITICAL", "UNKNOWN"})
_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_LABELS = {
    "LOW": "LOW",
    "MODERATE": "MEDIUM",
    "MEDIUM": "MEDIUM",
    "HIGH": "HIGH",
    "CRITICAL": "CRITICAL",
}
_SCORE_KEYS = ("score", "severity", "base_score", "baseScore")


@dataclass(frozen=True)
class Finding:
    package: str
    version: str
    id: str
    aliases: tuple[str, ...]
    fix_versions: tuple[str, ...]
    severity: str

    @property
    def label(self) -> str:
        aliases = f" ({', '.join(self.aliases)})" if self.aliases else ""
        fixes = ", ".join(self.fix_versions) or "none"
        return (
            f"{self.package} {self.version}: {self.id}{aliases} "
            f"severity {self.severity}, fix versions: {fixes}"
        )


@dataclass(frozen=True)
class AuditException:
    id: str
    package: str
    justification: str
    expires: dt.date
    status: str


@dataclass
class Report:
    errors: list[str]
    warnings: list[str]
    checked: int


def normalize_name(name: str) -> str:
    """Normalize a distribution name as in PEP 503 (``Typing_Ext`` -> ``typing-ext``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _band(score: float) -> str:
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    return "LOW"


def parse_severity(raw: object) -> str:
    """Map a pip-audit/OSV severity value to LOW, MEDIUM, HIGH, CRITICAL or UNKNOWN."""
    if isinstance(raw, bool) or raw is None:
        return "UNKNOWN"
    if isinstance(raw, int | float):
        return _band(float(raw))
    if isinstance(raw, str):
        text = raw.strip().upper()
        if text in _LABELS:
            return _LABELS[text]
        try:
            return _band(float(text))
        except ValueError:
            return "UNKNOWN"
    if isinstance(raw, dict):
        table = cast(dict[str, Any], raw)
        return parse_severity([table[key] for key in _SCORE_KEYS if key in table])
    if isinstance(raw, list):
        known = [s for s in map(parse_severity, cast(list[Any], raw)) if s != "UNKNOWN"]
        return max(known, key=_RANK.__getitem__) if known else "UNKNOWN"
    return "UNKNOWN"


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in cast(list[Any], value))


def parse_findings(payload: str) -> tuple[list[Finding], list[str], int]:
    """Parse pip-audit JSON; return the findings, the skipped dependencies and the count."""
    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"input is not valid JSON ({exc.msg})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
        raise ValueError("input is not pip-audit JSON: missing 'dependencies' list")
    dependencies = cast(list[Any], cast(dict[str, Any], data)["dependencies"])
    findings: list[Finding] = []
    skipped: list[str] = []
    for raw in dependencies:
        dep = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        name = str(dep.get("name", "?"))
        if "skip_reason" in dep:
            skipped.append(f"{name}: {dep['skip_reason']}")
            continue
        vulns: Any = dep.get("vulns", [])
        for vuln in cast(list[Any], vulns if isinstance(vulns, list) else []):
            entry = cast(dict[str, Any], vuln) if isinstance(vuln, dict) else {}
            finding = Finding(
                package=name,
                version=str(dep.get("version", "?")),
                id=str(entry.get("id", "?")),
                aliases=_strings(entry.get("aliases")),
                fix_versions=_strings(entry.get("fix_versions")),
                severity=parse_severity(entry.get("severity")),
            )
            # pip-audit can repeat the same vulnerability for one dependency; report it once.
            if finding not in findings:
                findings.append(finding)
    return findings, skipped, len(dependencies)


def _date(value: object) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def parse_exceptions(data: dict[str, Any]) -> tuple[list[AuditException], list[str]]:
    """Validate ``[[exception]]`` tables; return the valid entries and the problems found."""
    raw_entries: Any = data.get("exception", [])
    if not isinstance(raw_entries, list):
        return [], ["audit-exceptions.toml: 'exception' must be an array of tables"]
    entries: list[AuditException] = []
    errors: list[str] = []
    for index, raw in enumerate(cast(list[Any], raw_entries), start=1):
        if not isinstance(raw, dict):
            errors.append(f"exception #{index}: must be a table")
            continue
        table = cast(dict[str, Any], raw)
        fields: dict[str, str] = {}
        for key in ("id", "package", "justification", "status"):
            value = table.get(key, "")
            fields[key] = value.strip() if isinstance(value, str) else ""
        label = f"exception #{index} ({fields['id'] or '?'})"
        problems = [f"missing or empty '{key}'" for key, value in fields.items() if not value]
        if fields["status"] and fields["status"] not in STATUSES:
            problems.append(f"status must be one of {', '.join(STATUSES)}")
        expires = _date(table.get("expires"))
        if expires is None:
            problems.append("missing or invalid 'expires' date (YYYY-MM-DD)")
        if problems or expires is None:
            errors.append(f"{label}: {'; '.join(problems)}")
            continue
        entries.append(AuditException(expires=expires, **fields))
    return entries, errors


def _matches(exception: AuditException, finding: Finding) -> bool:
    return normalize_name(exception.package) == normalize_name(finding.package) and (
        exception.id == finding.id or exception.id in finding.aliases
    )


def evaluate(
    findings: list[Finding],
    exceptions: list[AuditException],
    today: dt.date,
    exception_errors: list[str] | None = None,
    checked: int = 0,
) -> Report:
    """Apply the severity policy and the exceptions to the findings."""
    errors = list(exception_errors or [])
    warnings: list[str] = []
    used: set[int] = set()
    for exc in exceptions:
        if exc.expires < today:
            errors.append(
                f"expired exception: {exc.package} {exc.id} expired on {exc.expires.isoformat()}"
            )
    for finding in findings:
        matching = [i for i, exc in enumerate(exceptions) if _matches(exc, finding)]
        used.update(matching)
        valid = [i for i in matching if exceptions[i].expires >= today]
        if finding.severity not in BLOCKING:
            warnings.append(finding.label)
        elif not valid:
            errors.append(f"{finding.label} (no valid exception in audit-exceptions.toml)")
        else:
            warnings.append(f"{finding.label} (excepted: {exceptions[valid[0]].status})")
    for index, exc in enumerate(exceptions):
        if index not in used:
            errors.append(
                f"stale exception: {exc.package} {exc.id} matches no current pip-audit finding"
            )
    return Report(errors=errors, warnings=warnings, checked=checked)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--input", type=Path, help="read pip-audit JSON from this file")
    parser.add_argument("--exceptions", type=Path, default=EXCEPTIONS_PATH)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args(argv)

    payload = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    try:
        findings, skipped, checked = parse_findings(payload)
    except ValueError as exc:
        print(f"Audit check failed: {exc}")
        return 1
    with args.exceptions.open("rb") as handle:
        exceptions, exception_errors = parse_exceptions(tomllib.load(handle))
    report = evaluate(findings, exceptions, args.today, exception_errors, checked)

    print(f"Checked {report.checked} dependencies, {len(findings)} findings")
    for item in skipped:
        print(f"SKIPPED {item}")
    for warning in report.warnings:
        print(f"WARNING {warning}")
    if report.errors:
        print(f"Audit check failed ({len(report.errors)} problems):")
        for error in report.errors:
            print(f"  {error}")
        return 1
    print("Audit check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
