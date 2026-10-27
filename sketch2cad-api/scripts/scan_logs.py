"""Scan CI and test logs for coordinates and sketch file names (jotdown H6-8, REQ-11).

Usage (from ``sketch2cad-api/``)::

    uv run python scripts/scan_logs.py [--patterns FILE] [--names-file FILE ...] LOG [LOG ...]

``LOG`` may be ``-`` to read standard input. Two families of rules run on every line:

* coordinates: ``(x, y)`` tuples, ``[x, y]`` lists, ``x=``/``lat:``/``gps...=`` assignments,
  geometry JSON keys followed by a value (``"points": [[``), WKT (``POINT (``) and GPS
  degrees-minutes. Plain integers, versions, timings, hashes and pytest progress do not match.
* file names: any ``*.jpg|jpeg|png|tif|tiff|pdf|heic`` whose base name is not in the
  ``[filenames] allow`` list of the patterns file, plus every ``[names]`` entry of the patterns
  file and of each ``--names-file`` (one name per line, ``#`` comments). The names of the real
  validation set are never committed: CI passes them through ``--names-file``.

Findings print as ``LOG:LINE: RULE: SNIPPET`` where the matched text is replaced by
``[REDACTED]`` and every other digit by ``#``: the output never repeats a coordinate or a
file name. Exit codes: 0 clean, 1 at least one finding, 2 usage, configuration or I/O error.
This is a regular-expression scan: a clean result is evidence, not proof (docs/log-hygiene.md).
"""

import argparse
import re
import sys
import tomllib
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
PATTERNS_PATH = API_DIR / "scan_logs_patterns.toml"
STDIN = "-"
REDACTED = "[REDACTED]"
SNIPPET_LENGTH = 120
SNIPPET_CONTEXT = 40

_NUMBER = r"-?\d+(?:\.\d+)?"
COORDINATE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("coord-tuple", re.compile(rf"\(\s*{_NUMBER}\s*,\s*{_NUMBER}\s*[,)]")),
    # Not after a word character: pytest parameter ids such as ``test_parse[-4,20]``.
    ("coord-list", re.compile(rf"(?<!\w)\[\s*{_NUMBER}\s*,\s*{_NUMBER}\s*[,\]]")),
    (
        "coord-assignment",
        re.compile(r"\b(?:x|y|lat|lon|lng|latitude|longitude|gps\w*)\s*[=:]\s*-?\d", re.IGNORECASE),
    ),
    (
        "geometry-json",
        re.compile(
            r"""["'](?:points|vertices|geometry|coordinates|coords|candidates|anchor|p1|p2"""
            r"""|\w*_points|\w*_(?:m|mm|px))["']\s*:\s*[\[{(]"""
            r"""|["'](?:x|y|x_m|y_m|lat|lon)["']\s*:\s*-?\d"""
        ),
    ),
    (
        "wkt",
        re.compile(r"\b(?:MULTI)?(?:POINT|LINESTRING|POLYGON)\s*Z?\s*\(\s*\(?\s*-?\d"),
    ),
    ("gps-dms", re.compile(r"\d{1,3}\s*°\s*\d{1,2}\s*['′]")),
)
"""Coordinate rules; they extend ``COORDINATE_PATTERNS`` of ``tests/test_log_hygiene.py``."""

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NAME_CHAR = r"""[^\s"'`<>()\[\]{},;|]"""
_PATH_CHAR = r"[\w@%+.~-]"


class ScanError(Exception):
    """Configuration or input problem; the script exits with status 2."""


@dataclass(frozen=True)
class Rules:
    """Compiled rules of one scan."""

    filename: re.Pattern[str]
    allow: frozenset[str]
    names: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    snippet: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.snippet}"


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _string_list(table: dict[str, object], key: str, where: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):  # pyright: ignore[reportUnknownVariableType]
        raise ScanError(f"{where}.{key} must be a list of non-empty strings")
    return [_normalized(str(item)) for item in value]  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]


def _table(document: dict[str, object], key: str) -> dict[str, object]:
    value = document.get(key, {})
    if not isinstance(value, dict):
        raise ScanError(f"[{key}] must be a table")
    return value  # pyright: ignore[reportUnknownVariableType]


def glob_regex(glob: str) -> re.Pattern[str]:
    """A whole-token regex for ``glob`` (``*`` any token run, ``?`` one token character)."""
    body = "".join(
        f"{_NAME_CHAR}*" if char == "*" else _NAME_CHAR if char == "?" else re.escape(char)
        for char in glob
    )
    return re.compile(rf"(?:(?<=[/\\])|(?<!{_NAME_CHAR})){body}(?!{_NAME_CHAR})", re.IGNORECASE)


def literal_regex(name: str) -> re.Pattern[str]:
    return re.compile(re.escape(name), re.IGNORECASE)


def read_names_file(path: Path) -> list[str]:
    """Names of ``path``: one per line, blank lines and ``#`` comments ignored."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ScanError(f"cannot read names file {path}: {type(exc).__name__}") from exc
    lines = (line.strip() for line in text.splitlines())
    return [_normalized(line) for line in lines if line and not line.startswith("#")]


def load_rules(patterns_path: Path, names_files: Sequence[Path] = ()) -> Rules:
    """Compile the rules of ``patterns_path`` plus the names of every ``names_files``."""
    try:
        document = tomllib.loads(patterns_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ScanError(f"cannot read patterns {patterns_path}: {type(exc).__name__}") from exc
    filenames, names = _table(document, "filenames"), _table(document, "names")
    extensions = _string_list(filenames, "extensions", "filenames")
    if not extensions:
        raise ScanError("filenames.extensions must not be empty")
    alternatives = "|".join(re.escape(ext.lstrip(".")) for ext in extensions)
    filename = re.compile(
        rf"(?<![\w@%+.~/\\-])(?:{_PATH_CHAR}+[/\\])*{_PATH_CHAR}*\w\.(?:{alternatives})(?!\w)",
        re.IGNORECASE,
    )
    literals = _string_list(names, "literal", "names")
    for path in names_files:
        literals += read_names_file(path)
    compiled = [literal_regex(name) for name in literals]
    compiled += [glob_regex(glob) for glob in _string_list(names, "globs", "names")]
    allow = frozenset(name.casefold() for name in _string_list(filenames, "allow", "filenames"))
    return Rules(filename, allow, tuple(compiled))


def _base_name(path: str) -> str:
    return re.split(r"[/\\]", path)[-1].casefold()


def line_matches(line: str, rules: Rules) -> list[tuple[str, int, int]]:
    """``(rule, start, end)`` of every match on ``line``."""
    found = [
        (rule, match.start(), match.end())
        for rule, pattern in COORDINATE_RULES
        for match in pattern.finditer(line)
    ]
    found += [
        ("sketch-filename", match.start(), match.end())
        for match in rules.filename.finditer(line)
        if _base_name(match.group()) not in rules.allow
    ]
    found += [
        ("validation-name", match.start(), match.end())
        for pattern in rules.names
        for match in pattern.finditer(line)
        if match.end() > match.start()
    ]
    return found


def mask(line: str, spans: Iterable[tuple[int, int]]) -> str:
    """``line`` with ``spans`` replaced by ``[REDACTED]`` and other digits by ``#``."""
    pieces: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        if end <= cursor:
            continue
        pieces.append(re.sub(r"\d", "#", line[cursor : max(start, cursor)]))
        if start >= cursor:
            pieces.append(REDACTED)
        cursor = end
    pieces.append(re.sub(r"\d", "#", line[cursor:]))
    masked = "".join(pieces).strip()
    if len(masked) <= SNIPPET_LENGTH:
        return masked
    # A window of SNIPPET_LENGTH characters that starts shortly before the first redaction.
    start = max(0, min(masked.find(REDACTED) - SNIPPET_CONTEXT, len(masked) - SNIPPET_LENGTH))
    window = masked[start : start + SNIPPET_LENGTH]
    if start > 0:
        window = f"...{window[3:]}"
    if start + SNIPPET_LENGTH < len(masked):
        window = f"{window[:-3]}..."
    return window


def scan_text(text: str, path: str, rules: Rules) -> list[Finding]:
    findings: list[Finding] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = _normalized(_ANSI.sub("", raw))
        matches = line_matches(line, rules)
        if not matches:
            continue
        snippet = mask(line, [(start, end) for _, start, end in matches])
        for rule in sorted({rule for rule, _, _ in matches}):
            findings.append(Finding(path, number, rule, snippet))
    return findings


def read_log(path: str) -> str:
    try:
        data = sys.stdin.buffer.read() if path == STDIN else Path(path).read_bytes()
    except OSError as exc:
        raise ScanError(f"cannot read log {path}: {type(exc).__name__}") from exc
    return data.decode("utf-8", errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("logs", nargs="+", metavar="LOG", help="log file, or - for stdin")
    parser.add_argument("--patterns", type=Path, default=PATTERNS_PATH, help="patterns TOML")
    parser.add_argument(
        "--names-file",
        type=Path,
        action="append",
        default=[],
        help="extra names that must not appear (one per line); repeatable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logs: list[str] = args.logs
    try:
        if logs.count(STDIN) > 1:
            raise ScanError("standard input can be scanned once")
        rules = load_rules(args.patterns, args.names_file)
        findings = [finding for path in logs for finding in scan_text(read_log(path), path, rules)]
    except ScanError as exc:
        print(f"scan_logs: error: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding.render())
    lines = len({(finding.path, finding.line) for finding in findings})
    verdict = f"{len(findings)} finding(s) on {lines} line(s)" if findings else "clean"
    print(f"scan_logs: {verdict} in {len(logs)} log(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
