"""open_dxf_xvfb.sh: syntax, skip without LibreCAD/xvfb, pass and fail with stub tools.

LibreCAD, ``xvfb-run`` and ``timeout`` are replaced by small shell stubs on ``PATH``, so the
exit-status contract is tested anywhere; the real LibreCAD run is documented in
``docs/dxf-viewers.md``.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]
SCRIPT = API_DIR / "scripts" / "open_dxf_xvfb.sh"
GOLDEN_DXF = API_DIR / "tests" / "golden" / "plan.dxf"
BASH = shutil.which("bash") or "/bin/bash"

STUB_TIMEOUT = '#!/bin/sh\nshift 2\nexec "$@"\n'  # drops --kill-after=N SECONDS
STUB_XVFB_RUN = '#!/bin/sh\nshift\nexec "$@"\n'  # drops --auto-servernum
STUB_LIBRECAD_OK = '#!/bin/sh\n[ "$1" = dxf2pdf ] && [ "$2" = -o ] && printf "%%PDF-1.4" > "$3"\n'
STUB_LIBRECAD_CRASH = '#!/bin/sh\necho "segmentation fault" >&2\nexit 139\n'
STUB_LIBRECAD_NO_PDF = "#!/bin/sh\nexit 0\n"


def run(path: str, *args: str, required: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": path, "DXF_VIEWER_REQUIRED": "1" if required else "0"}
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [BASH, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )


def stub_path(tmp_path: Path, librecad: str) -> str:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (
        ("timeout", STUB_TIMEOUT),
        ("xvfb-run", STUB_XVFB_RUN),
        ("librecad", librecad),
    ):
        tool = stubs / name
        tool.write_text(body)
        tool.chmod(0o755)
    return f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}"


def test_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [BASH, "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert os.access(SCRIPT, os.X_OK)


def test_missing_tools_skip_with_status_zero_unless_required(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    skipped = run(str(empty), str(GOLDEN_DXF))
    assert skipped.returncode == 0
    assert skipped.stdout.startswith("SKIP: missing librecad xvfb-run timeout")
    required = run(str(empty), str(GOLDEN_DXF), required=True)
    assert required.returncode == 3
    assert "missing librecad" in required.stderr


@pytest.mark.parametrize("args", [(), ("missing.dxf",), ("a.dxf", "b.dxf")])
def test_usage_errors_exit_two(tmp_path: Path, args: tuple[str, ...]) -> None:
    assert run(stub_path(tmp_path, STUB_LIBRECAD_OK), *args).returncode == 2


def test_successful_load_exits_zero(tmp_path: Path) -> None:
    result = run(stub_path(tmp_path, STUB_LIBRECAD_OK), str(GOLDEN_DXF), required=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"OK: LibreCAD opened {GOLDEN_DXF}")


@pytest.mark.parametrize(
    ("librecad", "message"),
    [
        (STUB_LIBRECAD_CRASH, "FAIL: LibreCAD exited with status 139"),
        (STUB_LIBRECAD_NO_PDF, "FAIL: LibreCAD exited 0 but wrote no PDF"),
    ],
)
def test_failed_load_exits_one(tmp_path: Path, librecad: str, message: str) -> None:
    result = run(stub_path(tmp_path, librecad), str(GOLDEN_DXF), required=True)
    assert result.returncode == 1
    assert message in result.stderr
