#!/usr/bin/env bash
# Migration round trip: alembic upgrade head -> downgrade base -> upgrade head.
#
# Runs against DATABASE_URL and exits non-zero as soon as any step fails.
#
# Optional environment variables:
#   MIGRATION_ROUNDTRIP_SCHEMA  Existing schema to migrate (for example "public" on a
#                               clean CI database). It is neither created nor dropped.
#                               When unset, a throwaway schema
#                               "roundtrip_<timestamp>_<pid>_<random>" is created and
#                               dropped on exit, so nothing is left behind.
#   ALEMBIC_SCRIPT_LOCATION     Alternative migrations folder (overrides the
#                               script_location of alembic.ini).
#
# Usage (from sketch2cad-api/): bash scripts/migration_roundtrip.sh
set -euo pipefail

api_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$api_dir"

: "${DATABASE_URL:?DATABASE_URL must be set}"
export DATABASE_URL

config="alembic.ini"
tmp_config=""
own_schema=""

# Execute one SQL statement against DATABASE_URL.
run_sql() {
  uv run python -c '
import os, sys
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as connection:
    connection.execute(text(sys.argv[1]))
engine.dispose()
' "$1"
}

cleanup() {
  local status=$?
  if [[ -n "$own_schema" ]]; then
    run_sql "DROP SCHEMA IF EXISTS \"$own_schema\" CASCADE" ||
      echo "WARNING: could not drop throwaway schema $own_schema" >&2
  fi
  if [[ -n "$tmp_config" ]]; then
    rm -f "$tmp_config"
  fi
  exit "$status"
}
trap cleanup EXIT

if [[ -n "${ALEMBIC_SCRIPT_LOCATION:-}" ]]; then
  tmp_config="$(mktemp "${TMPDIR:-/tmp}/alembic_roundtrip.XXXXXX")"
  sed "s|^script_location = .*|script_location = ${ALEMBIC_SCRIPT_LOCATION}|" alembic.ini \
    >"$tmp_config"
  config="$tmp_config"
  echo "Using migrations from $ALEMBIC_SCRIPT_LOCATION"
fi

schema="${MIGRATION_ROUNDTRIP_SCHEMA:-}"
if [[ -z "$schema" ]]; then
  schema="roundtrip_$(date +%s)_$$_${RANDOM}"
  run_sql "CREATE SCHEMA \"$schema\""
  own_schema="$schema"
  echo "Using throwaway schema $schema"
elif [[ ! "$schema" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "ERROR: invalid MIGRATION_ROUNDTRIP_SCHEMA: $schema" >&2
  exit 2
else
  echo "Using schema $schema"
fi

alembic_cmd() {
  uv run alembic -c "$config" -x "schema=$schema" "$@"
}

echo "Step 1/3: alembic upgrade head"
alembic_cmd upgrade head
echo "Step 2/3: alembic downgrade base"
alembic_cmd downgrade base
echo "Step 3/3: alembic upgrade head"
alembic_cmd upgrade head
echo "Migration round trip OK"
