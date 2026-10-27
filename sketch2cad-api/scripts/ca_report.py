"""Report which acceptance criteria CA-01..CA-13 pass (spec section 16, jotdown H6-1).

Usage (from ``sketch2cad-api/``, with ``DATABASE_URL`` set like for the test suite, because
many labelled tests are PostgreSQL-backed)::

    uv run python scripts/ca_report.py [--acceptance acceptance.toml]

Steps:

1. For every marker ``caNN`` run ``pytest --collect-only -m caNN -q`` and read the node ids.
   A required criterion with zero tests fails the report.
2. Run every labelled test once (``pytest -m "ca01 or ... or ca13" --junitxml=...``) and map
   the JUnit results back to the node ids of each criterion.
3. Print a Markdown table; append it to ``$GITHUB_STEP_SUMMARY`` when that variable is set.

A criterion PASSES when it has at least one test and every one of its tests ran and passed;
a skipped, failed, errored or missing result makes it FAIL. CA-04 and CA-05 are required only
when ``[features] h5_built = true`` in ``acceptance.toml``; otherwise they are reported as
``N/A (not built)``. The exit code is 1 when a required criterion does not pass, 2 on a
configuration or collection error, 0 otherwise. The markers are applied from the mapping in
``tests/conftest.py``.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
ACCEPTANCE_PATH = API_DIR / "acceptance.toml"

# (code, marker, short name) in spec section 16 order.
CRITERIA: tuple[tuple[str, str, str], ...] = (
    ("CA-01", "ca01", "Original"),
    ("CA-02", "ca02", "Calibration"),
    ("CA-03", "ca03", "Reconstruction from dimensions"),
    ("CA-04", "ca04", "Suggestions"),
    ("CA-05", "ca05", "Human review"),
    ("CA-06", "ca06", "DXF"),
    ("CA-07", "ca07", "Scale"),
    ("CA-08", "ca08", "Reproducibility"),
    ("CA-09", "ca09", "Audit"),
    ("CA-10", "ca10", "States"),
    ("CA-11", "ca11", "Failures"),
    ("CA-12", "ca12", "Security"),
    ("CA-13", "ca13", "Licenses"),
)
H5_MARKERS = frozenset({"ca04", "ca05"})

# pytest exit codes of a collection: 0 = tests collected, 5 = none collected.
COLLECT_OK = (0, 5)

Outcome = str  # "passed" | "failed" | "skipped"
Collect = Callable[[str], list[str]]
RunTests = Callable[[Sequence[str]], Mapping[str, Outcome]]


class ReportError(Exception):
    """Configuration or collection problem; the report cannot be trusted."""


@dataclass(frozen=True)
class Row:
    code: str
    name: str
    required: bool
    collected: int
    passed: int
    failed: int
    skipped: int

    @property
    def status(self) -> str:
        if not self.required:
            return "N/A (not built)"
        if self.collected == 0:
            return "FAIL (no tests)"
        if self.passed == self.collected:
            return "PASS"
        return "FAIL"


def load_h5_built(path: Path) -> bool:
    """Read ``[features] h5_built`` (a boolean) from ``acceptance.toml``."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc
    features = data.get("features")
    value = features.get("h5_built") if isinstance(features, dict) else None
    if not isinstance(value, bool):
        raise ReportError(f"{path}: [features] h5_built must be true or false")
    return value


def parse_collected(stdout: str) -> list[str]:
    """Node ids printed by ``pytest --collect-only -q``: one per line up to the first blank line.

    Parametrization ids may contain spaces, so every line of that block with ``::`` counts.
    """
    block = stdout.split("\n\n", 1)[0]
    return [line.strip() for line in block.splitlines() if "::" in line]


def junit_key(nodeid: str) -> str:
    """The ``classname::name`` pair pytest writes to JUnit XML for ``nodeid``."""
    path, bracket, params = nodeid.partition("[")
    names = path.split("::")
    names[0] = re.sub(r"\.py$", "", names[0].replace("/", "."))
    names[-1] += bracket + params
    return f"{'.'.join(names[:-1])}::{names[-1]}"


def parse_junit(xml_text: str) -> dict[str, Outcome]:
    """Outcome per ``classname::name``; the worst result wins when a test appears twice."""
    rank = {"passed": 0, "skipped": 1, "failed": 2}
    outcomes: dict[str, Outcome] = {}
    for case in ET.fromstring(xml_text).iter("testcase"):  # noqa: S314 (our own pytest output)
        key = f"{case.get('classname', '')}::{case.get('name', '')}"
        tags = {child.tag for child in case}
        if tags & {"failure", "error"}:
            outcome = "failed"
        elif "skipped" in tags:
            outcome = "skipped"
        else:
            outcome = "passed"
        if rank[outcome] >= rank[outcomes.get(key, "passed")]:
            outcomes[key] = outcome
    return outcomes


def build_rows(
    collected: Mapping[str, Sequence[str]], outcomes: Mapping[str, Outcome], *, h5_built: bool
) -> list[Row]:
    """One row per criterion; a collected test without a result counts as failed."""
    rows: list[Row] = []
    for code, marker, name in CRITERIA:
        results = [outcomes.get(junit_key(nodeid), "failed") for nodeid in collected[marker]]
        rows.append(
            Row(
                code=code,
                name=name,
                required=h5_built or marker not in H5_MARKERS,
                collected=len(results),
                passed=results.count("passed"),
                failed=results.count("failed"),
                skipped=results.count("skipped"),
            )
        )
    return rows


def render_table(rows: Sequence[Row]) -> str:
    lines = [
        "| Criterion | Tests collected | Passed | Failed | Skipped | Status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.code} {row.name} | {row.collected} | {row.passed} | {row.failed} "
            f"| {row.skipped} | {row.status} |"
        )
    return "\n".join(lines)


def exit_code(rows: Sequence[Row]) -> int:
    return 1 if any(row.required and row.status != "PASS" for row in rows) else 0


def collect_marker(marker: str) -> list[str]:
    """``pytest --collect-only -m <marker> -q`` in the project environment."""
    command = [sys.executable, "-m", "pytest", "--collect-only", "-m", marker, "-q"]
    result = subprocess.run(  # noqa: S603
        command, cwd=API_DIR, capture_output=True, text=True, check=False
    )
    if result.returncode not in COLLECT_OK:
        raise ReportError(f"collection of {marker} failed:\n{result.stdout}{result.stderr}")
    return parse_collected(result.stdout)


def run_labelled_tests(markers: Sequence[str]) -> dict[str, Outcome]:
    """Run every test carrying one of ``markers`` once and return the JUnit outcomes."""
    with tempfile.TemporaryDirectory() as directory:
        junit = Path(directory) / "ca.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-m",
            " or ".join(markers),
            f"--junitxml={junit}",
        ]
        result = subprocess.run(command, cwd=API_DIR, check=False)  # noqa: S603
        print(f"pytest exit code: {result.returncode}", flush=True)
        if not junit.is_file():
            return {}
        return parse_junit(junit.read_text(encoding="utf-8"))


def report(
    h5_built: bool, collect: Collect = collect_marker, run: RunTests = run_labelled_tests
) -> tuple[str, int]:
    """Collect per criterion, run the labelled tests and return (table, exit code)."""
    markers = [marker for _, marker, _ in CRITERIA]
    collected = {marker: collect(marker) for marker in markers}
    outcomes = run(markers)
    rows = build_rows(collected, outcomes, h5_built=h5_built)
    return render_table(rows), exit_code(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--acceptance", type=Path, default=ACCEPTANCE_PATH)
    args = parser.parse_args(argv)
    try:
        h5_built = load_h5_built(args.acceptance)
        table, code = report(h5_built)
    except ReportError as exc:
        print(f"ca_report: {exc}", file=sys.stderr)
        return 2
    heading = f"## Acceptance criteria (h5_built = {str(h5_built).lower()})"
    print(f"\n{heading}\n\n{table}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"{heading}\n\n{table}\n")
    print("Acceptance gate passed" if code == 0 else "Acceptance gate FAILED")
    return code


if __name__ == "__main__":
    sys.exit(main())
