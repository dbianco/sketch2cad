"""The committed openapi.json matches the schema generated from the app; no database needed."""

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from fastapi import FastAPI

from sketch2cad.api.app import create_app

API_DIR = Path(__file__).resolve().parents[1]
SCRIPT = API_DIR / "scripts" / "export_openapi.py"
OPENAPI_PATH = API_DIR / "openapi.json"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_script = _load_script()
build_schema = cast(Callable[[FastAPI | None], dict[str, Any]], _script.build_schema)
render_schema = cast(Callable[[dict[str, Any]], str], _script.render_schema)
diff_schema = cast(Callable[[str, str], list[str]], _script.diff_schema)


def _committed() -> str:
    return OPENAPI_PATH.read_text(encoding="utf-8")


def test_committed_openapi_matches_generated_schema() -> None:
    generated = render_schema(build_schema(None))
    diff = diff_schema(_committed(), generated)
    assert diff == [], (
        "openapi.json is stale; run `uv run python scripts/export_openapi.py`:\n"
        + "\n".join(diff[:40])
    )


def test_rendering_is_deterministic_and_pinned() -> None:
    first = render_schema(build_schema(None))
    second = render_schema(build_schema(create_app()))
    assert first == second
    assert first.endswith("}\n") and not first.endswith("\n\n")
    schema = json.loads(first)
    assert schema["servers"] == [{"url": "/"}]
    assert schema["info"]["version"] == "0.1.0"


def test_extra_route_is_reported_as_drift() -> None:
    app = create_app()

    @app.get("/snapshot-drift-probe")
    def snapshot_drift_probe() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"probe": "ok"}

    diff = diff_schema(_committed(), render_schema(build_schema(app)))
    assert diff, "an extra route must change the generated schema"
    assert any("/snapshot-drift-probe" in line for line in diff if line.startswith("+"))
    assert "/snapshot-drift-probe" not in create_app().openapi()["paths"]


def test_check_flag_passes_on_committed_file_from_another_cwd(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "", "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "up to date" in result.stdout


def test_check_flag_fails_and_prints_diff_on_stale_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = tmp_path / "openapi.json"
    stale.write_text(_committed().replace('"title": "Sketch2CAD API"', '"title": "Stale"'))
    monkeypatch.setattr(_script, "OPENAPI_PATH", stale)
    main = cast(Callable[[list[str]], int], _script.main)

    assert main(["--check"]) == 1
    output = capsys.readouterr().out
    assert "out of date" in output
    assert '-    "title": "Stale"' in output
    assert stale.read_text().count('"title": "Stale"') == 1
