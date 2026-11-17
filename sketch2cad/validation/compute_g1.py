"""G-1 baseline from validation/timings.csv (task 001-116, REQ-20): standard library only.

    python validation/compute_g1.py [validation/timings.csv]

One row per sketch with both times (wide format). Prints the medians of each tool and the
median and interquartile range of the per-sketch ratio Sketch2CAD / FreeCAD. A measured
baseline, not a pass/fail test (PRD DEC-4). A time cell may be empty or ``abandoned``
(protocol in measure_protocol.md): that sketch is counted but left out of the ratio.
Output names rows by number, never by sketch file name (log hygiene, H6-8).
Exit codes: 0 computed, 1 no usable rows or invalid values, 2 unreadable file.
"""

from __future__ import annotations  # also runs on an older system Python

import csv
import statistics
import sys
from pathlib import Path

DEFAULT_CSV = Path(__file__).resolve().parent / "timings.csv"
COLUMNS = (
    "sketch",
    "practice",
    "first_method",
    "minutes_sketch2cad",
    "minutes_freecad",
    "date_sketch2cad",
    "date_freecad",
    "person",
    "notes",
)
ABANDONED = "abandoned"


def minutes(text: str) -> float | None:
    """Minutes with comma or dot decimals; None for an empty or ``abandoned`` cell."""
    value = text.strip().lower()
    if value in ("", ABANDONED):
        return None
    number = float(value.replace(",", "."))
    if not number > 0:
        raise ValueError(f"minutes must be positive, got {text!r}")
    return number


def summary(values: list[float]) -> str:
    """``median (IQR q1-q3)``; the IQR needs at least two values."""
    median = statistics.median(values)
    if len(values) < 2:
        return f"{median:.2f}"
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return f"{median:.2f} (IQR {q1:.2f}-{q3:.2f})"


def compute(rows: list[dict[str, str]]) -> list[str]:
    """Report lines for the rows of timings.csv; raises ``ValueError`` on bad values."""
    if not rows:
        raise ValueError("no sketches recorded")
    s2c: list[float] = []
    freecad: list[float] = []
    ratios: list[float] = []
    incomplete = 0
    for number, row in enumerate(rows, start=2):
        try:
            a, b = minutes(row["minutes_sketch2cad"]), minutes(row["minutes_freecad"])
        except ValueError as exc:
            raise ValueError(f"row {number}: {exc}") from None
        s2c += [a] if a is not None else []
        freecad += [b] if b is not None else []
        if a is None or b is None:
            incomplete += 1
        else:
            ratios.append(a / b)
    if not ratios:
        raise ValueError("no row has both times")
    return [
        f"sketches: {len(rows)} (with both times: {len(ratios)}, incomplete: {incomplete})",
        f"minutes Sketch2CAD median: {summary(s2c)}",
        f"minutes FreeCAD median: {summary(freecad)}",
        f"ratio Sketch2CAD/FreeCAD median: {summary(ratios)}",
        "G-1 is a measured baseline (no threshold in v1).",
    ]


def read_rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of a CSV whose header must be exactly ``columns``."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError(f"header must be: {','.join(columns)}")
        return list(reader)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_CSV
    try:
        lines = compute(read_rows(path, COLUMNS))
    except OSError as exc:
        print(f"cannot read {path.name}: {type(exc).__name__}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
