#!/usr/bin/env bash
# Backup: pg_dump of the database plus a tar of the object store, in a dated folder.
#
# Writes <target-dir>/<UTC timestamp>/ with:
#   pg.dump      pg_dump custom format (-Fc), restorable with pg_restore
#   objects.tar  tar of STORAGE_ROOT/objects (tmp/ holds unfinished uploads and is skipped)
#   MANIFEST     key=value lines: created_at, schema, objects (file count), pg_mode,
#                pg_dump_version
#   SHA256SUMS   SHA-256 of pg.dump and objects.tar
#
# The database is dumped before the objects are archived. Objects are never modified or
# deleted, so every object referenced by a row in the dump is already present when the
# tar runs.
#
# Required environment variables:
#   DATABASE_URL   postgresql[+driver]://user:password@host:port/database; the SQLAlchemy
#                  driver suffix (+psycopg) is ignored.
#
# Optional environment variables:
#   STORAGE_ROOT   object store root holding objects/ (default: storage, as in the app)
#   BACKUP_SCHEMA  dump only this schema (default: the whole database)
#   PG_CONTAINER   name of a running PostgreSQL container; pg_dump then runs inside it with
#                  `docker exec` (user, password and database come from DATABASE_URL, host
#                  and port are ignored). When unset and DATABASE_URL points at localhost,
#                  the running container that publishes that port is used if there is
#                  exactly one; otherwise the host pg_dump.
#
# Usage (from sketch2cad-api/): bash scripts/backup.sh <target-dir>
set -euo pipefail

usage() {
  echo "Usage: bash scripts/backup.sh <target-dir>" >&2
  exit 2
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ $# -eq 1 ]] || usage
target_root="$1"
storage_root="${STORAGE_ROOT:-storage}"
schema="${BACKUP_SCHEMA:-}"

if [[ -n "$schema" && ! "$schema" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "ERROR: invalid BACKUP_SCHEMA: $schema" >&2
  exit 2
fi

# Decode %XX escapes of a URL component.
url_decode() {
  printf '%b' "${1//%/\\x}"
}

# Export PGUSER, PGPASSWORD, PGHOST, PGPORT and PGDATABASE from DATABASE_URL.
parse_database_url() {
  local url="${DATABASE_URL:-}"
  local pattern='^postgres(ql)?(\+[A-Za-z0-9_]+)?://([^:@/]+)(:([^@]*))?@([^:/?]+)(:([0-9]+))?/([^?]+)'
  if [[ -z "$url" ]]; then
    echo "ERROR: DATABASE_URL must be set" >&2
    exit 2
  fi
  if [[ ! "$url" =~ $pattern ]]; then
    echo "ERROR: DATABASE_URL is not a postgresql://user:password@host:port/database URL" >&2
    exit 2
  fi
  PGUSER="$(url_decode "${BASH_REMATCH[3]}")"
  PGPASSWORD="$(url_decode "${BASH_REMATCH[5]}")"
  PGHOST="${BASH_REMATCH[6]}"
  PGPORT="${BASH_REMATCH[8]:-5432}"
  PGDATABASE="$(url_decode "${BASH_REMATCH[9]}")"
  export PGUSER PGPASSWORD PGHOST PGPORT PGDATABASE
}

# Run a PostgreSQL client tool on the host or inside PG_CONTAINER.
pg_run() {
  local tool="$1"
  shift
  if [[ -n "${PG_CONTAINER:-}" ]]; then
    docker exec -i -e PGUSER -e PGPASSWORD -e PGDATABASE "$PG_CONTAINER" "$tool" "$@"
  else
    "$tool" "$@"
  fi
}

# Choose where the PostgreSQL client tools run, in this order: the PG_CONTAINER container;
# the running container that publishes the DATABASE_URL port on localhost (its tools match
# the server version); the host binaries.
detect_pg_mode() {
  local tool="$1" published=""
  if [[ -z "${PG_CONTAINER:-}" ]] && command -v docker >/dev/null 2>&1 &&
    [[ "$PGHOST" == "localhost" || "$PGHOST" == "127.0.0.1" ]]; then
    published="$(docker ps --filter "publish=$PGPORT" --format '{{.Names}}' 2>/dev/null || true)"
    if [[ -n "$published" && "$published" != *$'\n'* ]]; then
      PG_CONTAINER="$published"
    fi
  fi
  if [[ -n "${PG_CONTAINER:-}" ]]; then
    command -v docker >/dev/null 2>&1 || fail "PG_CONTAINER is set but docker is not installed"
    [[ "$(docker inspect -f '{{.State.Running}}' "$PG_CONTAINER" 2>/dev/null)" == "true" ]] ||
      fail "container $PG_CONTAINER is not running"
    pg_mode="docker:$PG_CONTAINER"
  else
    command -v "$tool" >/dev/null 2>&1 ||
      fail "$tool not found on the host; install it or set PG_CONTAINER to a running PostgreSQL container"
    pg_mode="host"
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d ' ' -f 1
  else
    shasum -a 256 "$1" | cut -d ' ' -f 1
  fi
}

parse_database_url
detect_pg_mode pg_dump
[[ -d "$storage_root/objects" ]] || fail "object directory $storage_root/objects not found"
mkdir -p "$target_root"

backup_dir="$target_root/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$backup_dir" || fail "backup folder $backup_dir already exists"
complete=0

cleanup() {
  local status=$?
  if [[ "$complete" != "1" ]]; then
    echo "Removing incomplete backup $backup_dir" >&2
    rm -rf "$backup_dir"
  fi
  exit "$status"
}
trap cleanup EXIT

echo "Step 1/3: pg_dump ($pg_mode) of ${schema:-the whole database}"
if [[ -n "$schema" ]]; then
  pg_run pg_dump --format=custom --schema="$schema" >"$backup_dir/pg.dump"
else
  pg_run pg_dump --format=custom >"$backup_dir/pg.dump"
fi
[[ -s "$backup_dir/pg.dump" ]] || fail "pg_dump produced an empty file"

echo "Step 2/3: tar of $storage_root/objects"
# COPYFILE_DISABLE keeps macOS tar from adding AppleDouble (._*) entries.
COPYFILE_DISABLE=1 tar -C "$storage_root" -cf "$backup_dir/objects.tar" objects
object_count="$(find "$storage_root/objects" -type f | wc -l | tr -d ' ')"

echo "Step 3/3: MANIFEST and SHA256SUMS"
{
  echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "schema=${schema:-public}"
  echo "objects=$object_count"
  echo "pg_mode=$pg_mode"
  echo "pg_dump_version=$(pg_run pg_dump --version)"
} >"$backup_dir/MANIFEST"
{
  echo "$(sha256_of "$backup_dir/pg.dump")  pg.dump"
  echo "$(sha256_of "$backup_dir/objects.tar")  objects.tar"
} >"$backup_dir/SHA256SUMS"

complete=1
echo "Backup written to $backup_dir ($object_count objects)"
echo "BACKUP_DIR=$backup_dir"
