"""Export the FastAPI OpenAPI schema to ``openapi.json`` deterministically.

Usage (from any directory)::

    uv run python scripts/export_openapi.py          # rewrite openapi.json
    uv run python scripts/export_openapi.py --check  # exit 1 if openapi.json is stale

The app is built with ``create_app()``, which needs neither settings nor a database.
"""

import argparse
import difflib
import json
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from sketch2cad.api.app import create_app

API_DIR = Path(__file__).resolve().parents[1]
OPENAPI_PATH = API_DIR / "openapi.json"
PACKAGE_NAME = "sketch2cad"
# Relative server URL: generated clients resolve it against the origin serving the API.
PINNED_SERVERS: list[dict[str, str]] = [{"url": "/"}]
MAX_DIFF_LINES = 40


def build_schema(app: FastAPI | None = None) -> dict[str, Any]:
    """Return the OpenAPI schema with the volatile fields pinned."""
    target = app if app is not None else create_app()
    schema = dict(target.openapi())
    info = dict(schema.get("info", {}))
    info["version"] = version(PACKAGE_NAME)
    schema["info"] = info
    schema["servers"] = PINNED_SERVERS
    return schema


def render_schema(schema: dict[str, Any]) -> str:
    """Serialize the schema with sorted keys, 2-space indent and a trailing newline."""
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def diff_schema(expected: str, actual: str) -> list[str]:
    """Return the unified diff between the committed and the regenerated schema."""
    return list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile="openapi.json (committed)",
            tofile="openapi.json (generated)",
            lineterm="",
        )
    )


def read_committed(path: Path) -> str:
    """Return the committed schema text, or an empty string if the file does not exist."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_schema(rendered: str, path: Path) -> None:
    """Write the rendered schema as UTF-8 with LF line endings."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed openapi.json differs from the app schema",
    )
    args = parser.parse_args(argv)

    rendered = render_schema(build_schema())
    if not args.check:
        write_schema(rendered, OPENAPI_PATH)
        print(f"Wrote {OPENAPI_PATH}")
        return 0

    diff = diff_schema(read_committed(OPENAPI_PATH), rendered)
    if not diff:
        print(f"{OPENAPI_PATH.name} is up to date")
        return 0
    print(
        f"{OPENAPI_PATH.name} is out of date ({len(diff)} diff lines); "
        "run `uv run python scripts/export_openapi.py` and commit the result."
    )
    for line in diff[:MAX_DIFF_LINES]:
        print(line)
    if len(diff) > MAX_DIFF_LINES:
        print(f"... ({len(diff) - MAX_DIFF_LINES} more diff lines)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
