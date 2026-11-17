"""Fixtures of the ``qc`` job tests, reused by the failure-path tests.

The helpers live in other test folders, imported through the API directory; the fixtures
are imported here so the test module's parameters do not shadow imports.
"""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.jobs.test_qc_job import (  # noqa: E402
    app,
    case_id,
    factory,
    jobs_engine,
    jobs_schema,
    stack,
    storage_root,
)

__all__ = ["app", "case_id", "factory", "jobs_engine", "jobs_schema", "stack", "storage_root"]
