"""G-3 from validation/suggestions.csv (task 001-118, REQ-20, REQ-17): standard library only.

    python validation/compute_g3.py [validation/suggestions.csv]

One row per sketch: the polylines suggested and reviewed, how many were accepted unchanged
(from ``query_suggestion_acceptance.py``) and the two conversion times. Prints the pooled
and per-sketch median accepted-unchanged percentage and the median and interquartile
range of the time ratio with / without suggestions. A measured baseline, not a pass/fail
test (PRD DEC-4). A first line starting with ``# H5 no construido`` declares G-3 pending
(not the case: H5 is built). Output names rows by number, never by sketch file name.
Exit codes: 0 computed or declared pending, 1 no rows or invalid values, 2 unreadable.
"""

from __future__ import annotations  # also runs on an older system Python

import csv
import statistics
import sys
from pathlib import Path

DEFAULT_CSV = Path(__file__).resolve().parent / "suggestions.csv"
COLUMNS = (
    "sketch",
    "polylines_total",
    "accepted_unchanged",
    "accepted_unchanged_pct",
    "minutes_with_suggestions",
    "minutes_without_suggestions",
    "notes",
)
H5_NOT_BUILT = "# H5 no construido"
PCT_TOLERANCE = 0.05


def number(text: str) -> float:
    return float(text.strip().replace(",", "."))


def summary(values: list[float]) -> str:
    """``median (IQR q1-q3)``; the IQR needs at least two values."""
    median = statistics.median(values)
    if len(values) < 2:
        return f"{median:.2f}"
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return f"{median:.2f} (IQR {q1:.2f}-{q3:.2f})"


def compute(rows: list[dict[str, str]]) -> list[str]:
    """Report lines; raises ``ValueError`` on an empty file or an invalid row."""
    if not rows:
        raise ValueError("no sketches recorded")
    total = accepted = 0
    pcts: list[float] = []
    ratios: list[float] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            n, k = int(row["polylines_total"]), int(row["accepted_unchanged"])
            pct = number(row["accepted_unchanged_pct"])
            with_s = number(row["minutes_with_suggestions"])
            without = number(row["minutes_without_suggestions"])
        except ValueError:
            raise ValueError(f"row {row_number}: counts, percentage or minutes invalid") from None
        if not (0 <= k <= n and n > 0 and with_s > 0 and without > 0):
            raise ValueError(f"row {row_number}: need 0 <= accepted <= total, total and times > 0")
        if abs(pct - 100.0 * k / n) > PCT_TOLERANCE:
            raise ValueError(f"row {row_number}: accepted_unchanged_pct is not 100 * {k} / {n}")
        total, accepted = total + n, accepted + k
        pcts.append(pct)
        ratios.append(with_s / without)
    return [
        f"sketches: {len(rows)}, polylines reviewed: {total}, accepted unchanged: {accepted}",
        f"accepted unchanged (pooled): {100.0 * accepted / total:.1f} %",
        f"accepted unchanged per sketch, median %: {summary(pcts)}",
        f"time ratio with/without suggestions, median: {summary(ratios)}",
        "G-3 is a measured baseline (no threshold in v1).",
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_CSV
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            if handle.readline().startswith(H5_NOT_BUILT):
                print("H5 no construido: G-3, CA-04 and CA-05 are validación posterior")
                return 0
            handle.seek(0)
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != COLUMNS:
                raise ValueError(f"header must be: {','.join(COLUMNS)}")
            lines = compute(list(reader))
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
