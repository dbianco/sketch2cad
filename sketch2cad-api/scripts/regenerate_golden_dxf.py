"""Rewrite the golden DXF ``tests/golden/plan.dxf`` and print its SHA-256.

Usage (from ``sketch2cad-api/``)::

    uv run python scripts/regenerate_golden_dxf.py

The snapshot and export flags live in ``tests/export/test_golden_dxf.py``. Paste the printed
hash into ``GOLDEN_SHA256`` there and repeat the viewer checks of ``docs/dxf-viewers.md``.
"""

import sys
from pathlib import Path

EXPORT_TESTS_DIR = Path(__file__).resolve().parents[1] / "tests" / "export"


def main() -> int:
    sys.path.insert(0, str(EXPORT_TESTS_DIR))
    from test_golden_dxf import GOLDEN_PATH, golden_bytes, sha256

    data = golden_bytes()
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_bytes(data)
    print(f"wrote {GOLDEN_PATH} ({len(data)} bytes)")
    print(sha256(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
