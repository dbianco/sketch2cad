"""backup.sh and restore_check.sh: a backup restores cleanly and one changed byte fails.

The PostgreSQL client tools run on the host, or inside the container named by
``PG_CONTAINER`` when that variable is set; without either the tests are skipped.
"""

import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.orm import Session

from sketch2cad.audit import EventKind, append_event
from sketch2cad.db.models import CaseFile, IncidentType, Source, User, UserRole
from sketch2cad.db.session import create_db_engine
from sketch2cad.storage.filesystem import FilesystemStorage

API_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_DIR / "alembic.ini"
BACKUP_SCRIPT = API_DIR / "scripts" / "backup.sh"
RESTORE_SCRIPT = API_DIR / "scripts" / "restore_check.sh"
BACKUP_DIR_LINE = re.compile(r"^BACKUP_DIR=(.+)$", re.MULTILINE)
SCRATCH_DB_LINE = re.compile(r"^Using scratch database (\S+)$", re.MULTILINE)
SCRATCH_DIR_LINE = re.compile(r"^Using scratch directory (.+)$", re.MULTILINE)
PAYLOADS = (b"original sketch bytes", b"%PDF-1.4 fake pdf", b"\x89PNG fake raster")


def published_container(database_url: str) -> str | None:
    """The running container that publishes the DATABASE_URL port on localhost, if any."""
    url = make_url(database_url)
    if shutil.which("docker") is None or url.host not in ("localhost", "127.0.0.1"):
        return None
    result = subprocess.run(  # noqa: S603  (fixed command, no shell)
        ["docker", "ps", "--filter", f"publish={url.port or 5432}", "--format", "{{.Names}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    names = result.stdout.split()
    return names[0] if result.returncode == 0 and len(names) == 1 else None


def pg_tools_skip_reason(database_url: str) -> str | None:
    """Why the PostgreSQL client tools are unavailable, or ``None`` when they can run.

    Mirrors the scripts: PG_CONTAINER, then the container publishing the database port,
    then the host pg_dump/pg_restore.
    """
    if shutil.which("bash") is None or shutil.which("tar") is None:
        return "bash and tar are required to run the backup scripts"
    if os.environ.get("PG_CONTAINER"):
        if shutil.which("docker") is None:
            return "PG_CONTAINER is set but docker is not installed"
        return None
    if published_container(database_url) is not None:
        return None
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        return "neither pg_dump/pg_restore on PATH nor a PostgreSQL container (docker) is available"
    return None


@dataclass(frozen=True)
class Fixture:
    database_url: str
    schema: str
    storage_root: Path
    backup_dir: Path
    keys: tuple[str, ...]


def run_script(script: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603  (fixed command, no shell)
        [bash, str(script), *args],
        cwd=API_DIR,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def seed(database_url: str, schema: str, storage: FilesystemStorage) -> tuple[str, ...]:
    """Migrate ``schema`` and add sources, case file events and an auth event."""
    config = Config(str(ALEMBIC_INI))
    config.attributes["schema"] = schema
    config.attributes["skip_logging_config"] = True
    command.upgrade(config, "head")
    stored = [storage.put(io.BytesIO(payload)) for payload in PAYLOADS]
    engine = create_db_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with Session(engine) as session, session.begin():
            user = User(username="backup-tech", password_hash="x", role=UserRole.TECHNICIAN)  # noqa: S106
            session.add(user)
            session.flush()
            case_file = CaseFile(
                code="EXP-2026-0001",
                incident_type=IncidentType.INSPECTION,
                incident_date=date(2026, 10, 25),
                description="backup test",
                declared_location="test",
                created_by=user.id,
            )
            session.add(case_file)
            session.flush()
            append_event(session, case_file_id=case_file.id, kind=EventKind.CASE_FILE_CREATED)
            for obj, media_type, raster in (
                (stored[0], "image/jpeg", None),
                (stored[1], "application/pdf", stored[2].key),
            ):
                source = Source(
                    case_file_id=case_file.id,
                    original_filename="plan",
                    media_type=media_type,
                    byte_size=1,
                    sha256=obj.sha256,
                    storage_key=obj.key,
                    raster_key=raster,
                    uploaded_by=user.id,
                )
                session.add(source)
                session.flush()
                append_event(
                    session,
                    case_file_id=case_file.id,
                    kind=EventKind.SOURCE_INGESTED,
                    entity_id=source.id,
                )
            append_event(session, case_file_id=None, kind=EventKind.AUTH_LOGIN, actor_id=user.id)
    finally:
        engine.dispose()
    return tuple(obj.key for obj in stored)


@pytest.fixture(scope="module")
def backup(database_url: str) -> Iterator[Fixture]:
    """A seeded throwaway schema and storage, backed up once with backup.sh."""
    reason = pg_tools_skip_reason(database_url)
    if reason:
        pytest.skip(reason)
    schema = f"test_backup_{uuid.uuid4().hex}"
    admin = create_db_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        with tempfile.TemporaryDirectory(prefix="backup_test.") as workdir:
            storage_root = Path(workdir, "storage")
            keys = seed(database_url, schema, FilesystemStorage(storage_root))
            result = run_script(
                BACKUP_SCRIPT,
                str(Path(workdir, "backups")),
                DATABASE_URL=database_url,
                STORAGE_ROOT=str(storage_root),
                BACKUP_SCHEMA=schema,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            match = BACKUP_DIR_LINE.search(result.stdout)
            assert match is not None, result.stdout
            yield Fixture(database_url, schema, storage_root, Path(match.group(1)), keys)
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def assert_scratch_removed(database_url: str, output: str) -> None:
    """The scratch database and directory announced by restore_check.sh are gone."""
    db_match = SCRATCH_DB_LINE.search(output)
    dir_match = SCRATCH_DIR_LINE.search(output)
    assert db_match is not None and dir_match is not None, output
    assert not Path(dir_match.group(1)).exists()
    engine = create_db_engine(database_url)
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_match.group(1)},
            ).scalar()
    finally:
        engine.dispose()
    assert exists is None


def test_backup_writes_dump_archive_and_checksums(backup: Fixture) -> None:
    for name in ("pg.dump", "objects.tar", "SHA256SUMS", "MANIFEST"):
        assert (backup.backup_dir / name).is_file(), name
    with tarfile.open(backup.backup_dir / "objects.tar") as archive:
        files = {m.name for m in archive.getmembers() if m.isfile()}
    assert files == {f"objects/{key}" for key in backup.keys}
    manifest = (backup.backup_dir / "MANIFEST").read_text()
    assert f"schema={backup.schema}\n" in manifest
    assert "objects=3\n" in manifest


def test_restore_check_passes_on_an_unmodified_backup(backup: Fixture) -> None:
    result = run_script(
        RESTORE_SCRIPT,
        str(backup.backup_dir),
        DATABASE_URL=backup.database_url,
        STORAGE_ROOT=str(backup.storage_root),
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Objects checked: 3 valid" in output
    assert "Objects compared with" in output
    assert "Sources checked: 2" in output
    assert "Event chains checked: 2" in output
    assert "PASS: backup" in output
    assert "FAIL" not in output
    assert_scratch_removed(backup.database_url, output)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """A temporary directory removed after the test (not kept by pytest's retention)."""
    with tempfile.TemporaryDirectory(prefix="backup_test.") as directory:
        yield Path(directory)


def test_restore_check_fails_naming_an_object_with_one_changed_byte(
    backup: Fixture, tmp_path: Path
) -> None:
    tampered = tmp_path / "tampered"
    shutil.copytree(backup.backup_dir, tampered)
    extracted = tmp_path / "extracted"
    with tarfile.open(tampered / "objects.tar") as archive:
        archive.extractall(extracted, filter="data")
    victim_key = backup.keys[1]
    victim = extracted / "objects" / victim_key
    victim.chmod(0o644)
    data = bytearray(victim.read_bytes())
    data[0] ^= 0x01
    victim.write_bytes(bytes(data))
    with tarfile.open(tampered / "objects.tar", "w") as archive:
        archive.add(extracted / "objects", arcname="objects")

    result = run_script(RESTORE_SCRIPT, str(tampered), DATABASE_URL=backup.database_url)
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert f"FAIL object {victim_key}: content SHA-256 is" in output
    assert f"object {victim_key} missing or invalid" in output
    assert "PASS" not in output
    for key in backup.keys:
        if key != victim_key:
            assert f"FAIL object {key}" not in output
    assert_scratch_removed(backup.database_url, output)
