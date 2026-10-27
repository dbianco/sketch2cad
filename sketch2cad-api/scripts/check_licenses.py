"""Fail when an installed package has a license outside the allow-list (decision DEC-6).

Usage (from any directory)::

    uv run python scripts/check_licenses.py                  # inspect the project environment
    uv run python scripts/check_licenses.py --input lic.json # inspect a saved pip-licenses JSON

With the ocr extra installed (from sketch2cad-api/), pass both exception files::

    uv run --extra ocr python scripts/check_licenses.py \\
        --exceptions license-exceptions.toml --exceptions license-exceptions-ocr.toml

Rules:

* Licenses listed in ``licenses-allowed.txt`` (MIT, BSD, Apache-2.0, PSF) pass automatically.
* GPL and AGPL licenses are forbidden; no exception can allow them.
* Any other license (LGPL, ISC, MIT-0, UNKNOWN, ...) passes only through a matching entry in
  ``license-exceptions.toml`` (same package, same version or ``"*"``, same license) that has a
  non-empty reason. Entries with ``status = "pending-owner-decision"`` pass with a warning.
* Exceptions that match no installed package are stale and fail the check.
* ``--exceptions`` can be given several times; the entries of all files are combined. Without
  it only ``license-exceptions.toml`` is read. Entries for the optional ``ocr`` extra live in
  ``license-exceptions-ocr.toml``, which is passed only when that extra is installed, so they
  never become stale in the default environment.

License strings are normalized from pip-licenses output: classifier names such as
``"MIT License"``, SPDX identifiers, SPDX expressions with ``OR``/``AND`` and several
classifiers joined with ``;`` (treated as ``AND``).
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

API_DIR = Path(__file__).resolve().parents[1]
ALLOWED_PATH = API_DIR / "licenses-allowed.txt"
EXCEPTIONS_PATH = API_DIR / "license-exceptions.toml"
PYPROJECT_PATH = API_DIR / "pyproject.toml"

STATUSES = ("approved", "pending-owner-decision")

Verdict = Literal["allowed", "needs-exception", "forbidden"]
_RANK: dict[Verdict, int] = {"forbidden": 0, "needs-exception": 1, "allowed": 2}

# Lower-cased pip-licenses spellings mapped to a normalized SPDX-like identifier.
ALIASES: dict[str, str] = {
    "mit": "MIT",
    "mit license": "MIT",
    "the mit license": "MIT",
    "mit-0": "MIT-0",
    "bsd": "BSD",
    "bsd license": "BSD",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd 2-clause license": "BSD-2-Clause",
    "simplified bsd": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache license version 2.0": "Apache-2.0",
    "psf": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "python-2.0": "PSF-2.0",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
    "gnu lesser general public license v3 or later (lgplv3+)": "LGPL-3.0-or-later",
    "gnu lesser general public license v2 (lgplv2)": "LGPL-2.0",
    "gnu library or lesser general public license (lgpl)": "LGPL",
    "lgplv3": "LGPL-3.0",
    "lgplv3+": "LGPL-3.0-or-later",
    "gnu general public license v3 (gplv3)": "GPL-3.0",
    "gnu general public license v3 or later (gplv3+)": "GPL-3.0-or-later",
    "gnu general public license v2 (gplv2)": "GPL-2.0",
    "gnu general public license v2 or later (gplv2+)": "GPL-2.0-or-later",
    "gnu general public license (gpl)": "GPL",
    "gplv3": "GPL-3.0",
    "gplv2": "GPL-2.0",
    "gnu affero general public license v3": "AGPL-3.0",
    "gnu affero general public license v3 or later (agplv3+)": "AGPL-3.0-or-later",
    "agplv3": "AGPL-3.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "isc license (iscl)": "ISC",
    "isc license": "ISC",
    "unknown": "UNKNOWN",
    "": "UNKNOWN",
}

_FORBIDDEN = re.compile(r"^A?GPL\b|^(gnu )?(affero )?general public license", re.IGNORECASE)
_TOKEN = re.compile(r"\(|\)|[^()]+")


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    license: str


@dataclass(frozen=True)
class LicenseException:
    package: str
    version: str
    license: str
    reason: str
    status: str


@dataclass
class Report:
    errors: list[str]
    warnings: list[str]
    checked: int


def normalize_name(name: str) -> str:
    """Normalize a distribution name as in PEP 503 (``Typing_Ext`` -> ``typing-ext``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def normalize_term(term: str) -> str:
    """Normalize a single license name; unknown spellings are returned trimmed."""
    cleaned = " ".join(term.split())
    return ALIASES.get(cleaned.lower(), cleaned)


def _tokens(raw: str) -> list[str]:
    """Split a license string into terms, operators (``AND``/``OR``) and parentheses."""
    out: list[str] = []
    for part in raw.split(";"):
        if out:
            out.append("AND")
        if " ".join(part.split()).lower() in ALIASES:
            # Classifier names such as "... v3 (LGPLv3)" contain parentheses.
            out.append(normalize_term(part))
            continue
        for chunk in _TOKEN.findall(part):
            if chunk in "()":
                out.append(chunk)
                continue
            for piece in re.split(r"\s+(AND|OR)\s+", f" {chunk} "):
                piece = piece.strip()
                if piece in ("AND", "OR"):
                    out.append(piece)
                elif piece:
                    # "Apache-2.0 WITH LLVM-exception" is judged by its base license.
                    out.append(normalize_term(re.split(r"\s+WITH\s+", piece)[0]))
    return out or ["UNKNOWN"]


def normalize_license(raw: str) -> str:
    """Return the canonical form of a license, e.g. ``"MIT License; BSD"`` -> ``"MIT AND BSD"``."""
    return " ".join(_tokens(raw)).replace("( ", "(").replace(" )", ")")


def classify_term(term: str, allowed: frozenset[str]) -> Verdict:
    if term in allowed:
        return "allowed"
    if _FORBIDDEN.search(term):
        return "forbidden"
    return "needs-exception"


def classify(raw: str, allowed: frozenset[str]) -> Verdict:
    """Classify a license string: ``OR`` takes the best alternative, ``AND`` the worst."""
    tokens = _tokens(raw)
    position = 0

    def parse_or() -> Verdict:
        nonlocal position
        best = parse_and()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            best = max(best, parse_and(), key=_RANK.__getitem__)
        return best

    def parse_and() -> Verdict:
        nonlocal position
        worst = parse_atom()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            worst = min(worst, parse_atom(), key=_RANK.__getitem__)
        return worst

    def parse_atom() -> Verdict:
        nonlocal position
        if position >= len(tokens):
            return "needs-exception"
        current = tokens[position]
        position += 1
        if current == "(":
            verdict = parse_or()
            if position < len(tokens) and tokens[position] == ")":
                position += 1
            return verdict
        if current in ("AND", "OR", ")"):
            return "needs-exception"
        return classify_term(current, allowed)

    verdict = parse_or()
    if position != len(tokens):
        return min(verdict, "needs-exception", key=_RANK.__getitem__)
    return verdict


def load_allowed(path: Path) -> frozenset[str]:
    """Read one license identifier per line; ``#`` starts a comment."""
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            ids.add(entry)
    return frozenset(ids)


def parse_exceptions(data: dict[str, Any]) -> tuple[list[LicenseException], list[str]]:
    """Validate ``[[exception]]`` tables; return the valid entries and the problems found."""
    entries: list[LicenseException] = []
    errors: list[str] = []
    raw_entries: Any = data.get("exception", [])
    if not isinstance(raw_entries, list):
        return [], ["license-exceptions.toml: 'exception' must be an array of tables"]
    for index, raw in enumerate(cast(list[Any], raw_entries), start=1):
        if not isinstance(raw, dict):
            errors.append(f"exception #{index}: must be a table")
            continue
        table = cast(dict[str, Any], raw)
        fields: dict[str, str] = {}
        for key in ("package", "version", "license", "reason", "status"):
            value = table.get(key, "")
            fields[key] = value.strip() if isinstance(value, str) else ""
        label = f"exception #{index} ({fields['package'] or '?'})"
        problems = [f"missing or empty '{key}'" for key, value in fields.items() if not value]
        if fields["status"] and fields["status"] not in STATUSES:
            problems.append(f"status must be one of {', '.join(STATUSES)}")
        if problems:
            errors.append(f"{label}: {'; '.join(problems)}")
            continue
        entries.append(LicenseException(**fields))
    return entries, errors


def load_exceptions(paths: list[Path]) -> tuple[list[LicenseException], list[str]]:
    """Read and combine the exceptions of several TOML files; problems name their file."""
    entries: list[LicenseException] = []
    errors: list[str] = []
    for path in paths:
        with path.open("rb") as handle:
            file_entries, file_errors = parse_exceptions(tomllib.load(handle))
        entries.extend(file_entries)
        errors.extend(f"{path.name}: {error}" for error in file_errors)
    return entries, errors


def _matches(exception: LicenseException, package: Package) -> bool:
    return (
        normalize_name(exception.package) == normalize_name(package.name)
        and exception.version in ("*", package.version)
        and normalize_license(exception.license) == normalize_license(package.license)
    )


def evaluate(
    packages: list[Package],
    allowed: frozenset[str],
    exceptions: list[LicenseException],
    exception_errors: list[str] | None = None,
) -> Report:
    """Check every package against the allow-list and the exceptions."""
    errors = list(exception_errors or [])
    warnings: list[str] = []
    used: set[int] = set()
    for package in sorted(packages, key=lambda item: normalize_name(item.name)):
        verdict = classify(package.license, allowed)
        label = f"{package.name} {package.version}: {package.license}"
        if verdict == "allowed":
            continue
        match = next(
            (i for i, exc in enumerate(exceptions) if _matches(exc, package)),
            None,
        )
        if match is not None:
            used.add(match)
        if verdict == "forbidden":
            errors.append(f"{label} (GPL/AGPL is forbidden; exceptions cannot allow it)")
        elif match is None:
            errors.append(f"{label} (not in the allow-list and no matching exception)")
        elif exceptions[match].status != "approved":
            warnings.append(f"{label} (exception {exceptions[match].status})")
    for index, exc in enumerate(exceptions):
        if index not in used:
            errors.append(
                f"stale exception: {exc.package} {exc.version} ({exc.license}) "
                "matches no installed package that needs it"
            )
    return Report(errors=errors, warnings=warnings, checked=len(packages))


def parse_packages(payload: str, skip: frozenset[str] = frozenset()) -> list[Package]:
    """Parse ``pip-licenses --format=json`` output, ignoring the names in ``skip``."""
    items: Any = json.loads(payload)
    packages: list[Package] = []
    for item in items:
        name = str(item["Name"])
        if normalize_name(name) in skip:
            continue
        packages.append(Package(name, str(item["Version"]), str(item.get("License", ""))))
    return packages


def run_pip_licenses() -> str:
    """Run pip-licenses with the interpreter of the project environment (itself included)."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "piplicenses", "--format=json", "--from=mixed", "--with-system"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def project_name(path: Path) -> str:
    with path.open("rb") as handle:
        return normalize_name(str(tomllib.load(handle)["project"]["name"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--input", type=Path, help="read pip-licenses JSON from this file")
    parser.add_argument("--allowed", type=Path, default=ALLOWED_PATH)
    parser.add_argument(
        "--exceptions",
        type=Path,
        action="append",
        help=f"exceptions TOML file; repeat to combine files (default: {EXCEPTIONS_PATH.name})",
    )
    args = parser.parse_args(argv)

    payload = args.input.read_text(encoding="utf-8") if args.input else run_pip_licenses()
    own = project_name(PYPROJECT_PATH)
    packages = parse_packages(payload, skip=frozenset({own}))
    exceptions, exception_errors = load_exceptions(args.exceptions or [EXCEPTIONS_PATH])
    report = evaluate(packages, load_allowed(args.allowed), exceptions, exception_errors)

    print(f"Checked {report.checked} packages (skipped the project itself: {own})")
    for warning in report.warnings:
        print(f"WARNING {warning}")
    if report.errors:
        print(f"License check failed ({len(report.errors)} problems):")
        for error in report.errors:
            print(f"  {error}")
        return 1
    print("License check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
