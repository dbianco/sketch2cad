"""G-2 from validation/dimension_check.csv (task 001-117, REQ-20): standard library only.

    python validation/compute_g2.py [validation/dimension_check.csv]

The first line names the second reviewer, ``# reviewer: <initials>``; the CSV header
follows. Each row is one declared dimension: the value written on the sketch and the
value read in the exported DXF, both in metres (comma or dot decimals).

Match rule (:func:`dimension_match`): the exported value, rounded half-up to the decimals
of the written value, equals the written value. When the exported value has fewer
decimals than the written one they must be numerically equal (a lost digit is a
mismatch). The percentage uses this computed match; the reviewer's ``match`` column
(``yes``/``no``, optional) is cross-checked and disagreements are listed for re-checking.
Output names rows by number, never by sketch file name (log hygiene, H6-8).
Exit codes: 0 computed, 1 no rows, no reviewer or invalid values, 2 unreadable file.
"""

from __future__ import annotations  # also runs on an older system Python

import csv
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

DEFAULT_CSV = Path(__file__).resolve().parent / "dimension_check.csv"
COLUMNS = ("sketch", "dimension_id", "written_value_m", "exported_value_m", "match", "notes")
REVIEWER_PREFIX = "# reviewer:"
TARGET_PCT = 99.0
FLAGS = {"yes": True, "si": True, "sí": True, "no": False}


def value_m(text: str) -> Decimal:
    """A positive value in metres from ``4,20`` or ``4.20``."""
    try:
        value = Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"not a number: {text!r}") from None
    if not value.is_finite() or value <= 0:
        raise ValueError(f"not a positive value: {text!r}")
    return value


def _decimals(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def dimension_match(written: str, exported: str) -> bool:
    """Whether the exported value reproduces the written one at the written precision."""
    w, e = value_m(written), value_m(exported)
    places = _decimals(w)
    if _decimals(e) < places:
        return e == w
    return e.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) == w


def read_check(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Reviewer initials and rows; raises ``ValueError`` without a reviewer or header."""
    with path.open(newline="", encoding="utf-8") as handle:
        first = handle.readline().strip()
        if not first.lower().startswith(REVIEWER_PREFIX):
            raise ValueError(f"first line must be '{REVIEWER_PREFIX} <initials>'")
        reviewer = first[len(REVIEWER_PREFIX) :].strip()
        if not reviewer:
            raise ValueError("the second reviewer's initials are missing")
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"header must be: {','.join(COLUMNS)}")
        return reviewer, list(reader)


def compute(reviewer: str, rows: list[dict[str, str]]) -> list[str]:
    """Report lines; raises ``ValueError`` on an empty file or an invalid row."""
    if not rows:
        raise ValueError("no dimensions recorded")
    matches = 0
    lines: list[str] = []
    for number, row in enumerate(rows, start=3):
        try:
            ok = dimension_match(row["written_value_m"], row["exported_value_m"])
        except ValueError as exc:
            raise ValueError(f"row {number}: {exc}") from None
        matches += ok
        flag = row["match"].strip().lower()
        if flag and FLAGS.get(flag) is not ok:
            lines.append(f"row {number}: reviewer flag '{flag}' differs from the rule; re-check")
    pct = 100.0 * matches / len(rows)
    verdict = "met" if pct >= TARGET_PCT else "NOT met"
    return [
        f"reviewer: {reviewer}",
        f"dimensions: {len(rows)}, matching: {matches}, mismatching: {len(rows) - matches}",
        f"G-2 match: {pct:.1f} % (target >= {TARGET_PCT:.0f} %: {verdict})",
        *lines,
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_CSV
    try:
        lines = compute(*read_check(path))
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
