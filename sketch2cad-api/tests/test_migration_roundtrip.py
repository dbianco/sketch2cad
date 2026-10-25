"""The migration round-trip script succeeds on clean migrations and fails on broken ones."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

from sketch2cad.db.session import create_db_engine

API_DIR = Path(__file__).resolve().parents[1]
SCRIPT = API_DIR / "scripts" / "migration_roundtrip.sh"
SCHEMA_LINE = re.compile(r"^Using throwaway schema (\S+)$", re.MULTILINE)
STEPS = (
    "Step 1/3: alembic upgrade head",
    "Step 2/3: alembic downgrade base",
    "Step 3/3: alembic upgrade head",
)
BROKEN_REVISION = '''"""Deliberately broken downgrade.

Revision ID: 0999_broken_downgrade
Revises: 0001_initial
"""

revision = "0999_broken_downgrade"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("deliberately broken downgrade")
'''


def run_script(database_url: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    """Run the round-trip script with a throwaway schema and return its result."""
    bash = shutil.which("bash")
    assert bash is not None, "bash is required to run the round-trip script"
    env = {**os.environ, "DATABASE_URL": database_url, **extra_env}
    env.pop("MIGRATION_ROUNDTRIP_SCHEMA", None)
    return subprocess.run(  # noqa: S603  (fixed command, no shell)
        [bash, str(SCRIPT)],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def assert_schema_dropped(database_url: str, output: str) -> None:
    """The throwaway schema announced by the script no longer exists."""
    match = SCHEMA_LINE.search(output)
    assert match is not None, output
    engine = create_db_engine(database_url)
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_namespace WHERE nspname = :name"),
                {"name": match.group(1)},
            ).scalar()
    finally:
        engine.dispose()
    assert exists is None


def test_roundtrip_succeeds_and_prints_the_three_steps(database_url: str) -> None:
    result = run_script(database_url)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    positions = [result.stdout.index(step) for step in STEPS]
    assert positions == sorted(positions)
    assert output.count("Running upgrade  -> 0001_initial") == 2
    assert "Running downgrade 0001_initial -> " in output
    assert_schema_dropped(database_url, result.stdout)


def test_roundtrip_fails_on_broken_downgrade(database_url: str, tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    shutil.copytree(
        API_DIR / "migrations", migrations, ignore=shutil.ignore_patterns("__pycache__")
    )
    (migrations / "versions" / "0999_broken_downgrade.py").write_text(BROKEN_REVISION)

    result = run_script(database_url, ALEMBIC_SCRIPT_LOCATION=str(migrations))
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "deliberately broken downgrade" in output
    assert "Step 3/3" not in result.stdout
    assert_schema_dropped(database_url, result.stdout)


@pytest.mark.parametrize("schema", ["bad-name", "x; DROP SCHEMA public"])
def test_roundtrip_rejects_invalid_schema_names(database_url: str, schema: str) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    env = {**os.environ, "DATABASE_URL": database_url, "MIGRATION_ROUNDTRIP_SCHEMA": schema}
    result = subprocess.run(  # noqa: S603  (fixed command, no shell)
        [bash, str(SCRIPT)], cwd=API_DIR, env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode == 2
    assert "invalid MIGRATION_ROUNDTRIP_SCHEMA" in result.stderr
