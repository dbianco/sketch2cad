"""Central logging setup for the API and the worker (REQ-11, H1-9).

Application logs carry identifiers only (case file, source, job and user ids): never file
names, image bytes, EXIF values, geometry or coordinates. :func:`configure_logging`
installs one ``key=value`` handler on the root logger and holds back third-party loggers
that print payload content at verbose levels (Pillow logs EXIF tag values at DEBUG,
SQLAlchemy logs statement parameters at INFO). As a safeguard, the handler replaces bytes
arguments with their length and renders exceptions as their type and frame locations,
without the exception message, which may quote user data.

The root level comes from ``LOG_LEVEL`` (default ``INFO``); the held-back loggers stay
at ``WARNING`` whatever ``LOG_LEVEL`` says.
"""

import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime
from typing import TextIO

DEFAULT_LEVEL = "INFO"
LOG_LEVEL_ENV = "LOG_LEVEL"

QUIET_LOGGERS = (
    "PIL",  # EXIF/TIFF tag values at DEBUG
    "sqlalchemy",  # SQL statement parameters (file names, EXIF JSON) at INFO
    "python_multipart",  # temporary file names and paths at INFO
    "multipart",  # legacy name of python_multipart
)
"""Third-party loggers held at ``WARNING``: they can emit payload content when verbose."""


class PayloadRedactionFilter(logging.Filter):
    """Replace bytes-like arguments of a record with ``<bytes len=N>``."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redacted(arg) for arg in args)
        elif isinstance(args, dict):
            record.args = {key: _redacted(value) for key, value in args.items()}
        record.msg = _redacted(record.msg)
        return True


def _redacted(value: object) -> object:
    if isinstance(value, bytes | bytearray):
        return f"<bytes len={len(value)}>"
    if isinstance(value, memoryview):
        return f"<bytes len={value.nbytes}>"
    return value


class KeyValueFormatter(logging.Formatter):
    """One ``ts=... level=... logger=... msg="..."`` line per record."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        fields = [
            f"ts={timestamp}",
            f"level={record.levelname}",
            f"logger={record.name}",
            f"msg={json.dumps(record.getMessage(), ensure_ascii=False)}",
        ]
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, _, tb = record.exc_info
            frames = "; ".join(
                f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
                for frame in traceback.extract_tb(tb)
            )
            fields.append(f"exc_type={exc_type.__name__}")
            fields.append(f"exc_frames={json.dumps(frames)}")
        return " ".join(fields)


class _Sketch2CadHandler(logging.StreamHandler[TextIO]):
    """Marker type, so repeated :func:`configure_logging` calls add a single handler."""


def configure_logging(level: str | None = None) -> None:
    """Install the identifiers-only handler on the root logger; safe to call repeatedly.

    ``level`` overrides ``LOG_LEVEL``; an unknown level name falls back to ``INFO``.
    """
    name = (level or os.environ.get(LOG_LEVEL_ENV) or DEFAULT_LEVEL).upper()
    resolved = logging.getLevelNamesMapping().get(name, logging.INFO)
    root = logging.getLogger()
    if not any(isinstance(handler, _Sketch2CadHandler) for handler in root.handlers):
        handler = _Sketch2CadHandler(sys.stderr)
        handler.setFormatter(KeyValueFormatter())
        handler.addFilter(PayloadRedactionFilter())
        root.addHandler(handler)
    root.setLevel(resolved)
    for quiet in QUIET_LOGGERS:
        logging.getLogger(quiet).setLevel(max(resolved, logging.WARNING))
