"""Log scan for coordinates and sketch file names (task 001-120, H6-8, REQ-11); no database."""

import importlib.util
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

API_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = API_DIR.parent
SCRIPT = API_DIR / "scripts" / "scan_logs.py"
PATTERNS = API_DIR / "scan_logs_patterns.toml"
FIXTURE_DIRS = (
    API_DIR / "tests" / "fixtures",
    REPO_DIR / "sketch2cad-web" / "e2e" / "fixtures",
    REPO_DIR / "docs" / "img",
)
SKETCH_NAME = "croquis-validacion-07.jpg"
STEM_NAME = "levantamiento-ruta-9-km-12"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scan_logs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_logs"] = module
    spec.loader.exec_module(module)
    return module


_script = _load_script()
main = cast(Callable[[Sequence[str]], int], _script.main)
REDACTED = cast(str, _script.REDACTED)

# Lines captured from a real local run of the CI commands (`uv run pytest -q`, the same run
# with `-o log_cli=true --log-cli-level=DEBUG`, `ruff check`, `ruff format --check`, `pyright`),
# plus two hand-written lines: a hash with versions, and allow-listed fixture names.
CLEAN_LOG = (API_DIR / "tests" / "fixtures" / "scan_logs_clean.log").read_text(encoding="utf-8")

COORDINATE_LINES = [
    ("coord-tuple", "Snapped vertex to (12.5, 3.25)"),
    ("coord-tuple", "Point(4.2137, -0.5)"),
    ("coord-list", 'msg="moved entity to [4.2137, 3.0]"'),
    ("coord-list", "coords [[0.0, 0.0], [4.2, 0.0]]"),
    ("coord-assignment", "fix applied x=4.2137 y=3.0"),
    ("coord-assignment", "GPSLatitude: 40.4168"),
    ("geometry-json", '{"kind": "polyline", "coords_m": [[0.0, 0.0]]}'),
    ("geometry-json", "{'points': [[1, 2]]}"),
    ("geometry-json", '"geometry": {"kind": "point"}'),
    ("geometry-json", '"polygon_px":[[10,20],[30,40]]'),
    ("wkt", "LINESTRING (0 0, 4.2 0)"),
    ("gps-dms", "lat 40° 25' 12.3\" N"),
]


def _write(tmp_path: Path, text: str, name: str = "ci.log") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _names(tmp_path: Path, *names: str) -> Path:
    path = tmp_path / "names.txt"
    path.write_text("# validation set\n\n" + "\n".join(names) + "\n", encoding="utf-8")
    return path


def _run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        cwd=API_DIR,
    )


def test_a_realistic_clean_log_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(_write(tmp_path, CLEAN_LOG))]) == 0
    assert "clean" in capsys.readouterr().out


def test_a_known_sketch_file_name_from_the_names_file_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = _write(tmp_path, CLEAN_LOG + f"ERROR could not read /data/{SKETCH_NAME.upper()}\n")
    bare = tmp_path / "bare.toml"
    bare.write_text('[filenames]\nextensions = ["heic"]\n', encoding="utf-8")

    assert main(["--patterns", str(bare), str(log)]) == 0
    capsys.readouterr()
    assert (
        main(
            ["--patterns", str(bare), "--names-file", str(_names(tmp_path, SKETCH_NAME)), str(log)]
        )
        == 1
    )
    out = capsys.readouterr().out
    assert f"ci.log:{CLEAN_LOG.count(chr(10)) + 1}: validation-name:" in out


def test_any_unlisted_sketch_file_name_exits_1_with_the_default_patterns(tmp_path: Path) -> None:
    log = _write(tmp_path, f"Ingested {SKETCH_NAME} and Croquis Plaza.HEIC\n")

    assert main([str(log)]) == 1


def test_a_name_without_extension_is_found_only_through_the_names_file(tmp_path: Path) -> None:
    log = _write(tmp_path, f"Source {STEM_NAME} queued\n")

    assert main([str(log)]) == 0
    assert main(["--names-file", str(_names(tmp_path, STEM_NAME)), str(log)]) == 1


def test_validation_set_globs_match_paths_without_extension(tmp_path: Path) -> None:
    log = _write(tmp_path, "copying /srv/s2c/validation/sketches/c01-cruce to storage\n")

    assert main([str(log)]) == 1


@pytest.mark.parametrize(("rule", "line"), COORDINATE_LINES)
def test_coordinates_exit_1_with_their_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], rule: str, line: str
) -> None:
    assert main([str(_write(tmp_path, f"{CLEAN_LOG}{line}\n"))]) == 1
    assert f": {rule}: " in capsys.readouterr().out


def test_findings_never_echo_the_coordinates_or_the_names(tmp_path: Path) -> None:
    secrets = ["4.2137", "-0.5", "40.4168", SKETCH_NAME, "croquis-validacion", STEM_NAME]
    leak = (
        f"WARNING Export failed for {SKETCH_NAME}: vertex (4.2137, -0.5)\n"
        f'INFO moved {STEM_NAME} "coords_m": [[4.2137, 40.4168]] lat=40.4168\n'
    )

    result = _run("--names-file", str(_names(tmp_path, STEM_NAME)), "-", stdin=leak)

    assert result.returncode == 1
    assert "-:1: coord-tuple:" in result.stdout and "-:1: sketch-filename:" in result.stdout
    assert "-:2: geometry-json:" in result.stdout and "-:2: validation-name:" in result.stdout
    assert REDACTED in result.stdout
    assert not [secret for secret in secrets if secret in result.stdout + result.stderr]


def test_masked_snippet_hides_digits_and_is_bounded_around_the_first_match() -> None:
    mask = cast(Callable[[str, list[tuple[int, int]]], str], _script.mask)
    short = "moved 7a0c4b1e to (12.5, 3.25) then " + "x" * 200
    long = "w" * 300 + " moved to (12.5, 3.25) " + "z" * 300

    head = mask(short, [(18, 30)])
    middle = mask(long, [(310, 322)])

    assert head.startswith(f"moved #a#c#b#e to {REDACTED} then ")
    assert len(head) == 120 and head.endswith("...")
    assert len(middle) == 120 and middle.startswith("...") and middle.endswith("...")
    assert f"moved to {REDACTED} zzz" in middle


def test_names_file_entries_reach_the_output_only_masked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = _write(tmp_path, f"Ingested {STEM_NAME} for plan 2\n")

    assert main(["--names-file", str(_names(tmp_path, STEM_NAME)), str(log)]) == 1
    captured = capsys.readouterr()
    assert STEM_NAME not in captured.out + captured.err
    assert f"Ingested {REDACTED} for plan #" in captured.out


@pytest.mark.parametrize(
    "line",
    [
        "Uploaded tests/fixtures/one_page.pdf",
        "fixture e2e/fixtures/SYNTHETIC_SKETCH.png loaded",
        "package members plan.pdf, audit.pdf and original.png",
        "media types image/png, image/jpeg and application/pdf; suffix .png",
    ],
)
def test_allow_listed_fixture_names_and_media_types_pass(tmp_path: Path, line: str) -> None:
    assert main([str(_write(tmp_path, line + "\n"))]) == 0


def test_every_committed_image_or_pdf_fixture_is_allow_listed() -> None:
    config = tomllib.loads(PATTERNS.read_text(encoding="utf-8"))
    allow = {name.casefold() for name in config["filenames"]["allow"]}
    extensions = {f".{ext}" for ext in config["filenames"]["extensions"]}
    fixtures = {
        path.name.casefold()
        for directory in FIXTURE_DIRS
        for path in directory.iterdir()
        if path.suffix.casefold() in extensions
    }

    assert fixtures and fixtures <= allow, fixtures - allow
    assert config["names"]["literal"] == [], "real validation names belong in --names-file"


def test_stdin_mode_reads_the_log_from_standard_input() -> None:
    assert _run("-", stdin=CLEAN_LOG).returncode == 0
    assert _run("-", stdin=f"opened {SKETCH_NAME}\n").returncode == 1


@pytest.mark.parametrize(
    "args",
    [
        ["missing.log"],
        ["--names-file", "missing-names.txt", "ci.log"],
        ["--patterns", "missing.toml", "ci.log"],
        ["-", "-"],
        [],
    ],
)
def test_usage_and_io_errors_exit_2(tmp_path: Path, args: list[str]) -> None:
    _write(tmp_path, CLEAN_LOG)
    resolved = [
        str(tmp_path / arg) if arg.endswith((".log", ".txt", ".toml")) else arg for arg in args
    ]

    result = _run(*resolved, stdin="")

    assert result.returncode == 2, result.stdout + result.stderr


def test_an_unreadable_log_exits_2(tmp_path: Path) -> None:
    assert main([str(tmp_path)]) == 2  # a directory cannot be read as a log
