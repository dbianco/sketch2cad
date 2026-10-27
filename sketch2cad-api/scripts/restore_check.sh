#!/usr/bin/env bash
# Restore check: restore a backup written by backup.sh into a scratch database and a
# scratch directory, then verify it. The source database and object store are only read.
#
# Checks (every failure is printed as a "FAIL ..." line; all checks run before exiting):
#   1. pg.dump and objects.tar match SHA256SUMS.
#   2. Every restored object file under objects/sha256/ab/cd/<hash> has a canonical path
#      and its SHA-256 equals its name. With STORAGE_ROOT set, the object also has the
#      same SHA-256 as the source object, when the source has it.
#   3. Every `source` row resolves to a valid object: storage_key is the key of its
#      sha256 and raster_key, when set, is present.
#   4. Every frozen revision keeps its snapshot: the SHA-256 of the canonical (RFC 8785)
#      bytes of revision.snapshot, recomputed with
#      sketch2cad.revisions.snapshot.snapshot_sha256, equals revision.snapshot_sha256 and
#      the snapshot_sha256 of its revision.frozen event, when present. Snapshots live in
#      the database (JSONB), not in the object store.
#   5. The hash chain of every case file and the global auth chain verifies
#      (sketch2cad.audit.verify.verify_chain) in the restored database.
#
# The report prints "Originals matched: M of N" (source rows whose objects passed checks 2
# and 3) and "Snapshots matched: M of N" (frozen revisions that passed check 4).
#
# The scratch database "restore_check_<timestamp>_<pid>_<random>" is created next to the
# DATABASE_URL database and dropped on exit, as is the mktemp scratch directory.
#
# Required environment variables:
#   DATABASE_URL   postgresql[+driver]://user:password@host:port/database; the role needs
#                  CREATEDB. It must be reachable from this host (the checks use uv).
#
# Optional environment variables:
#   STORAGE_ROOT   source object store to compare against (no comparison when unset)
#   PG_CONTAINER   name of a running PostgreSQL container that serves DATABASE_URL;
#                  pg_restore then runs inside it with `docker exec`. When unset and
#                  DATABASE_URL points at localhost, the running container that publishes
#                  that port is used if there is exactly one; otherwise the host pg_restore
#                  (its major version must not be newer than the server's).
#
# Exit status: 0 when every check passes, 1 when a check fails, 2 on usage errors.
#
# Usage (from sketch2cad-api/): bash scripts/restore_check.sh <backup-dir>
set -euo pipefail

usage() {
  echo "Usage: bash scripts/restore_check.sh <backup-dir>" >&2
  exit 2
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ $# -eq 1 ]] || usage
backup_dir="$(cd "$1" 2>/dev/null && pwd)" || fail "backup folder $1 not found"
source_root=""
if [[ -n "${STORAGE_ROOT:-}" ]]; then
  source_root="$(cd "$STORAGE_ROOT" && pwd)"
fi
for name in pg.dump objects.tar SHA256SUMS MANIFEST; do
  [[ -f "$backup_dir/$name" ]] || fail "$backup_dir/$name not found"
done
schema="$(sed -n 's/^schema=//p' "$backup_dir/MANIFEST")"
schema="${schema:-public}"
[[ "$schema" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "invalid schema in MANIFEST: $schema"

api_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$api_dir"

# Decode %XX escapes of a URL component.
url_decode() {
  printf '%b' "${1//%/\\x}"
}

# Export PGUSER, PGPASSWORD, PGHOST and PGPORT from DATABASE_URL.
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
  export PGUSER PGPASSWORD PGHOST PGPORT
}

# Run a PostgreSQL client tool on the host or inside PG_CONTAINER.
pg_run() {
  local tool="$1"
  shift
  if [[ -n "${PG_CONTAINER:-}" ]]; then
    docker exec -i -e PGUSER -e PGPASSWORD "$PG_CONTAINER" "$tool" "$@"
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

# Execute one statement on the DATABASE_URL database outside a transaction.
admin_sql() {
  uv run python -c '
import os, sys
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"], isolation_level="AUTOCOMMIT")
with engine.connect() as connection:
    connection.execute(text(sys.argv[1]))
engine.dispose()
' "$1"
}

parse_database_url
detect_pg_mode pg_restore

scratch_db=""
scratch_dir=""

cleanup() {
  local status=$?
  if [[ -n "$scratch_db" ]]; then
    admin_sql "DROP DATABASE IF EXISTS \"$scratch_db\" WITH (FORCE)" ||
      echo "WARNING: could not drop scratch database $scratch_db" >&2
  fi
  if [[ -n "$scratch_dir" ]]; then
    rm -rf "$scratch_dir"
  fi
  exit "$status"
}
trap cleanup EXIT

tmp_base="${TMPDIR:-/tmp}"
scratch_dir="$(mktemp -d "${tmp_base%/}/restore_check.XXXXXX")"
echo "Using scratch directory $scratch_dir"
echo "Step 1/3: extract objects.tar"
tar -C "$scratch_dir" -xf "$backup_dir/objects.tar"
[[ -d "$scratch_dir/objects" ]] || fail "objects.tar has no objects/ folder"

name="restore_check_$(date +%s)_$$_${RANDOM}"
echo "Step 2/3: pg_restore ($pg_mode) into scratch database $name"
admin_sql "CREATE DATABASE \"$name\""
scratch_db="$name"
echo "Using scratch database $scratch_db"
pg_run pg_restore --no-owner --no-privileges --exit-on-error --dbname="$scratch_db" \
  <"$backup_dir/pg.dump" ||
  fail "pg_restore into $scratch_db failed; it must not be older than the pg_dump that wrote the backup (MANIFEST) nor newer than the server"

echo "Step 3/3: verify objects, sources, snapshots and event chains"
if ! uv run python - "$backup_dir" "$scratch_dir/objects" "$scratch_db" "$schema" "$source_root" <<'PY'
import hashlib
import itertools
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, make_url, select, text
from sqlalchemy.orm import Session

from sketch2cad.audit import event_content
from sketch2cad.audit.verify import verify_chain
from sketch2cad.db.models import Event
from sketch2cad.revisions.snapshot import snapshot_sha256
from sketch2cad.storage.base import InvalidKeyError, key_for_hash, validate_key

backup_dir, objects_dir, database, schema, source_root = sys.argv[1:6]
objects_root = Path(objects_dir)
failures: list[str] = []


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


for line in (Path(backup_dir) / "SHA256SUMS").read_text().splitlines():
    expected, _, archive = line.partition("  ")
    if file_sha256(Path(backup_dir) / archive) != expected:
        failures.append(f"FAIL archive {archive}: SHA-256 differs from SHA256SUMS")

valid_keys: set[str] = set()
not_in_source = 0
for path in sorted(p for p in objects_root.rglob("*") if not p.is_dir()):
    key = path.relative_to(objects_root).as_posix()
    try:
        name = validate_key(key)
    except InvalidKeyError:
        failures.append(f"FAIL object {key}: not a content-addressed object path")
        continue
    actual = file_sha256(path)
    if actual != name:
        failures.append(f"FAIL object {key}: content SHA-256 is {actual}")
        continue
    valid_keys.add(key)
    if source_root:
        source_path = Path(source_root, "objects", key)
        if not source_path.is_file():
            not_in_source += 1
        elif file_sha256(source_path) != actual:
            failures.append(f"FAIL object {key}: differs from the source object")
print(f"Objects checked: {len(valid_keys)} valid")
if source_root:
    print(f"Objects compared with {source_root}: {len(valid_keys) - not_in_source}")

url = make_url(os.environ["DATABASE_URL"]).set(database=database)
engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
try:
    with Session(engine) as session:
        for table in ("source", "event", "revision"):
            if session.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is None:
                failures.append(f"FAIL table {schema}.{table}: missing in the restored database")
                raise SystemExit("\n".join(failures))
        rows = session.execute(text("SELECT id, sha256, storage_key, raster_key FROM source"))
        source_count = 0
        sources_matched = 0
        for source_id, sha256, storage_key, raster_key in rows:
            source_count += 1
            matched = True
            if storage_key != key_for_hash(sha256):
                failures.append(f"FAIL source {source_id}: storage_key does not match sha256")
                matched = False
            for key in (storage_key, raster_key):
                if key is not None and key not in valid_keys:
                    failures.append(f"FAIL source {source_id}: object {key} missing or invalid")
                    matched = False
            sources_matched += matched
        print(f"Sources checked: {source_count}")
        print(f"Originals matched: {sources_matched} of {source_count}")

        frozen_events: dict[object, list[object]] = {}
        for revision_id, after in session.execute(
            text(
                "SELECT revision_id, after FROM event"
                " WHERE kind = 'revision.frozen' AND revision_id IS NOT NULL ORDER BY id"
            )
        ):
            if isinstance(after, dict) and "snapshot_sha256" in after:
                frozen_events.setdefault(revision_id, []).append(after["snapshot_sha256"])
        revisions = session.execute(
            text(
                "SELECT id, snapshot, snapshot_sha256 FROM revision"
                " WHERE state = 'frozen' ORDER BY id"
            )
        )
        snapshot_count = 0
        snapshots_matched = 0
        for revision_id, snapshot, stored in revisions:
            snapshot_count += 1
            try:
                actual = snapshot_sha256(snapshot)
            except (TypeError, ValueError) as exc:
                failures.append(f"FAIL revision {revision_id}: snapshot not canonicalizable ({exc})")
                continue
            matched = True
            if actual != stored:
                failures.append(
                    f"FAIL revision {revision_id}: snapshot SHA-256 is {actual}, "
                    f"snapshot_sha256 is {stored}"
                )
                matched = False
            for recorded in frozen_events.get(revision_id, []):
                if recorded != actual:
                    failures.append(
                        f"FAIL revision {revision_id}: snapshot SHA-256 is {actual}, "
                        f"revision.frozen event has {recorded}"
                    )
                    matched = False
            snapshots_matched += matched
        print(f"Snapshots matched: {snapshots_matched} of {snapshot_count}")

        events = session.scalars(
            select(Event).order_by(Event.case_file_id.nulls_first(), Event.id)
        )
        chains = 0
        for case_file_id, group in itertools.groupby(events, key=lambda e: e.case_file_id):
            chains += 1
            chain = [{**event_content(e), "hash": e.hash} for e in group]
            result = verify_chain(chain)
            if not result.valid:
                label = "auth chain" if case_file_id is None else f"case file {case_file_id}"
                failures.append(
                    f"FAIL event chain of {label}: event {result.first_invalid_id} "
                    f"{result.reason} ({result.detail})"
                )
        print(f"Event chains checked: {chains}")
finally:
    engine.dispose()

for failure in failures:
    print(failure)
sys.exit(1 if failures else 0)
PY
then
  fail "restore check of $backup_dir found problems"
fi
echo "PASS: backup $backup_dir restored and verified"
