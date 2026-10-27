"""Acceptance-criterion labels and report (task 001-113, H6-1); no database needed."""

import ast
import importlib.util
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

API_DIR = Path(__file__).resolve().parents[1]
SCRIPT = API_DIR / "scripts" / "ca_report.py"
MARKERS = [f"ca{n:02d}" for n in range(1, 14)]


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_script = _load(SCRIPT, "ca_report")
# Loaded under its own name: ``import conftest`` could resolve to another conftest.py.
_conftest = _load(Path(__file__).with_name("conftest.py"), "sketch2cad_tests_conftest")
CA_TESTS = cast(dict[str, tuple[str, ...]], _conftest.CA_TESTS)
ca_markers_for = cast(Callable[[str], list[str]], _conftest.ca_markers_for)
parse_collected = cast(Callable[[str], list[str]], _script.parse_collected)
parse_junit = cast(Callable[[str], dict[str, str]], _script.parse_junit)
junit_key = cast(Callable[[str], str], _script.junit_key)
build_rows = cast(Callable[..., list[Any]], _script.build_rows)
render_table = cast(Callable[[Sequence[Any]], str], _script.render_table)
exit_code = cast(Callable[[Sequence[Any]], int], _script.exit_code)
report = cast(Callable[..., tuple[str, int]], _script.report)
load_h5_built = cast(Callable[[Path], bool], _script.load_h5_built)
main = cast(Callable[[list[str]], int], _script.main)


# --- Mapping in tests/conftest.py -----------------------------------------------------------


def _definitions(tree: ast.Module) -> dict[str, set[str]]:
    """Top-level test functions and classes with their method names."""
    found: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found[node.name] = set()
        elif isinstance(node, ast.ClassDef):
            found[node.name] = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
    return found


def test_every_mapping_entry_names_an_existing_file_and_test() -> None:
    stale: list[str] = []
    for marker, entries in CA_TESTS.items():
        assert entries, marker
        for entry in entries:
            path, *names = entry.split("::")
            file = API_DIR / path
            if not (path.startswith("tests/") and file.is_file()):
                stale.append(f"{marker}: {entry}")
                continue
            defined = _definitions(ast.parse(file.read_text(encoding="utf-8")))
            if names and names[0] not in defined:
                stale.append(f"{marker}: {entry}")
            elif len(names) == 2 and names[1] not in defined[names[0]]:
                stale.append(f"{marker}: {entry}")
            elif len(names) > 2:
                stale.append(f"{marker}: {entry}")
    assert stale == []


def test_the_mapping_covers_ca01_to_ca13_and_every_marker_is_registered() -> None:
    with (API_DIR / "pyproject.toml").open("rb") as handle:
        options = tomllib.load(handle)["tool"]["pytest"]["ini_options"]
    registered = {line.split(":", 1)[0] for line in options["markers"]}
    assert sorted(CA_TESTS) == MARKERS
    assert set(MARKERS) <= registered
    assert "--strict-markers" in options["addopts"]
    assert [marker for _, marker, _ in _script.CRITERIA] == MARKERS


def test_entries_match_their_tests_and_parametrizations_but_not_longer_names() -> None:
    entry = CA_TESTS["ca02"][0]
    assert "ca02" in ca_markers_for(entry)
    assert "ca02" in ca_markers_for(f"{entry}[case]")
    assert "ca02" not in ca_markers_for(f"{entry}_and_more")
    whole_file = "tests/geometry/test_cases_by_hand.py::test_fix_length_of_3_m_segment_to_4_20_m"
    assert ca_markers_for(whole_file) == ["ca03"]
    assert ca_markers_for("tests/test_config.py::test_anything") == []


# --- Report logic ---------------------------------------------------------------------------


def _rows(
    collected: Mapping[str, Sequence[str]], outcomes: Mapping[str, str], *, h5_built: bool
) -> list[Any]:
    full = {m: list(collected.get(m, [f"tests/t.py::test_{m}"])) for m in MARKERS}
    return build_rows(full, outcomes, h5_built=h5_built)


def _all_pass(collected: Mapping[str, Sequence[str]]) -> dict[str, str]:
    return {junit_key(n): "passed" for ids in collected.values() for n in ids}


def test_all_passing_criteria_pass_and_the_table_lists_ca01_to_ca13() -> None:
    collected = {marker: [f"tests/t.py::test_{marker}"] for marker in MARKERS}
    rows = _rows(collected, _all_pass(collected), h5_built=True)

    assert [row.status for row in rows] == ["PASS"] * 13
    assert exit_code(rows) == 0
    table = render_table(rows)
    for n in range(1, 14):
        assert f"| CA-{n:02d} " in table
    assert "| CA-03 Reconstruction from dimensions | 1 | 1 | 0 | 0 | PASS |" in table


def test_a_required_criterion_without_tests_fails_the_gate() -> None:
    collected: dict[str, list[str]] = {marker: [f"tests/t.py::test_{marker}"] for marker in MARKERS}
    collected["ca03"] = []
    rows = _rows(collected, _all_pass(collected), h5_built=True)

    assert rows[2].status == "FAIL (no tests)"
    assert exit_code(rows) == 1


@pytest.mark.parametrize("outcome", ["failed", "skipped", None])
def test_a_failed_skipped_or_missing_test_fails_its_criterion(outcome: str | None) -> None:
    collected = {marker: [f"tests/t.py::test_{marker}"] for marker in MARKERS}
    collected["ca07"].append("tests/t.py::test_other[1 m]")
    outcomes = _all_pass(collected)
    if outcome is None:
        del outcomes[junit_key("tests/t.py::test_other[1 m]")]
    else:
        outcomes[junit_key("tests/t.py::test_other[1 m]")] = outcome
    rows = _rows(collected, outcomes, h5_built=True)

    ca07 = rows[6]
    assert (ca07.collected, ca07.passed, ca07.status) == (2, 1, "FAIL")
    assert (ca07.skipped == 1) is (outcome == "skipped")
    assert exit_code(rows) == 1


@pytest.mark.parametrize(
    ("h5_built", "status", "code"), [(False, "N/A (not built)", 0), (True, "FAIL (no tests)", 1)]
)
def test_h5_built_switches_ca04_and_ca05_between_n_a_and_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    h5_built: bool,
    status: str,
    code: int,
) -> None:
    acceptance = tmp_path / "acceptance.toml"
    acceptance.write_text(f"[features]\nh5_built = {str(h5_built).lower()}\n", encoding="utf-8")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def collect(marker: str) -> list[str]:
        return [] if marker in {"ca04", "ca05"} else [f"tests/t.py::test_{marker}"]

    def run(markers: Sequence[str]) -> dict[str, str]:
        return {junit_key(f"tests/t.py::test_{m}"): "passed" for m in markers}

    def fake_report(h5: bool) -> tuple[str, int]:
        return report(h5, collect, run)

    monkeypatch.setattr(_script, "report", fake_report)

    assert load_h5_built(acceptance) is h5_built
    assert main(["--acceptance", str(acceptance)]) == code
    out = capsys.readouterr().out
    assert f"| CA-04 Suggestions | 0 | 0 | 0 | 0 | {status} |" in out
    assert f"| CA-05 Human review | 0 | 0 | 0 | 0 | {status} |" in out
    assert "| CA-13 Licenses | 1 | 1 | 0 | 0 | PASS |" in out


def test_the_table_is_appended_to_the_github_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("before\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    def fake_report(h5: bool) -> tuple[str, int]:
        return "| table |", 0

    monkeypatch.setattr(_script, "report", fake_report)

    assert main([]) == 0
    assert summary.read_text(encoding="utf-8").startswith("before\n## Acceptance criteria")
    assert "| table |" in summary.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "content", ["", "[features]\n", "[features]\nh5_built = 'yes'\n", "not toml ="]
)
def test_invalid_acceptance_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    acceptance = tmp_path / "acceptance.toml"
    acceptance.write_text(content, encoding="utf-8")
    assert main(["--acceptance", str(acceptance)]) == 2
    assert "h5_built" in capsys.readouterr().err or content == "not toml ="


def test_committed_acceptance_file_says_h5_is_built() -> None:
    assert load_h5_built(API_DIR / "acceptance.toml") is True


# --- Real pytest output ---------------------------------------------------------------------


SAMPLE_TESTS = """
import pytest

@pytest.mark.parametrize("case", ["3-4-5 nearest", "b"])
def test_param(case):
    assert case != "b"

class TestGroup:
    def test_method(self):
        pytest.skip("not here")

def test_plain():
    pass
"""


def test_collect_and_junit_output_of_a_real_pytest_run_map_to_node_ids(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(SAMPLE_TESTS, encoding="utf-8")
    base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--rootdir", str(tmp_path)]
    collect = subprocess.run(  # noqa: S603
        [*base, "--collect-only", "-q", "-ra"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    junit = tmp_path / "out.xml"
    run = subprocess.run(  # noqa: S603
        [*base, "-q", f"--junitxml={junit}"], cwd=tmp_path, capture_output=True, check=False
    )

    nodeids = parse_collected(collect.stdout)
    assert nodeids == [
        "test_sample.py::test_param[3-4-5 nearest]",
        "test_sample.py::test_param[b]",
        "test_sample.py::TestGroup::test_method",
        "test_sample.py::test_plain",
    ]
    assert run.returncode == 1
    outcomes = parse_junit(junit.read_text(encoding="utf-8"))
    assert [outcomes[junit_key(n)] for n in nodeids] == ["passed", "failed", "skipped", "passed"]
