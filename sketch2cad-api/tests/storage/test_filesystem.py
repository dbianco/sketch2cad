import hashlib
import io
import os
import stat
import threading
from pathlib import Path

import pytest

from sketch2cad.storage import (
    FilesystemStorage,
    InvalidKeyError,
    ObjectNotFoundError,
    S3Storage,
    StoredObject,
)

CONTENT = b"croquis original \x00\xff" * 1000


class FailingStream:
    """Yields some bytes and then raises, like a client that drops mid-upload."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def read(self, size: int = -1, /) -> bytes:
        if not self._chunks:
            raise OSError("connection reset mid-upload")
        return self._chunks.pop(0)


def _files_under(directory: Path) -> list[Path]:
    return [path for path in directory.rglob("*") if path.is_file()]


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemStorage:
    return FilesystemStorage(tmp_path / "store")


def test_put_returns_sha256_and_content_addressed_key(storage: FilesystemStorage) -> None:
    result = storage.put(io.BytesIO(CONTENT))

    digest = hashlib.sha256(CONTENT).hexdigest()
    assert result == StoredObject(sha256=digest, key=f"sha256/{digest[:2]}/{digest[2:4]}/{digest}")
    expected_path = storage.objects_dir / "sha256" / digest[:2] / digest[2:4] / digest
    assert _files_under(storage.objects_dir) == [expected_path]


def test_put_same_bytes_twice_keeps_key_mtime_and_content(storage: FilesystemStorage) -> None:
    first = storage.put(io.BytesIO(CONTENT))
    path = storage.objects_dir.joinpath(*first.key.split("/"))
    # Push the mtime into the past so any rewrite would be detectable.
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = path.stat()

    second = storage.put(io.BytesIO(CONTENT))

    after = path.stat()
    assert second == first
    assert after.st_mtime_ns == before.st_mtime_ns == 1_000_000_000
    assert after.st_ino == before.st_ino
    assert path.read_bytes() == CONTENT
    assert _files_under(storage.objects_dir) == [path]


def test_stored_file_is_read_only_and_get_matches_hash(storage: FilesystemStorage) -> None:
    result = storage.put(io.BytesIO(CONTENT))
    path = storage.objects_dir.joinpath(*result.key.split("/"))

    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    data = storage.get(result.key)
    assert hashlib.sha256(data).hexdigest() == result.sha256
    assert data == CONTENT


def test_put_of_failing_stream_leaves_no_file(storage: FilesystemStorage, tmp_path: Path) -> None:
    with pytest.raises(OSError, match="mid-upload"):
        storage.put(FailingStream([b"partial ", b"content"]))

    assert list(storage.objects_dir.rglob("*")) == []
    assert _files_under(tmp_path / "store") == []


def test_put_reads_stream_in_chunks(storage: FilesystemStorage) -> None:
    content = os.urandom(3 * 1024 * 1024 + 17)

    result = storage.put(io.BytesIO(content))

    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert storage.get(result.key) == content


def test_put_empty_stream_stores_empty_object(storage: FilesystemStorage) -> None:
    result = storage.put(io.BytesIO(b""))

    assert result.sha256 == hashlib.sha256(b"").hexdigest()
    assert storage.get(result.key) == b""


def test_concurrent_puts_of_same_bytes_store_one_object(storage: FilesystemStorage) -> None:
    results: list[StoredObject] = []
    lock = threading.Lock()

    def worker() -> None:
        result = storage.put(io.BytesIO(CONTENT))
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8
    assert len(set(results)) == 1
    assert len(_files_under(storage.objects_dir)) == 1
    assert _files_under(storage.objects_dir.parent / "tmp") == []


def test_exists_reports_stored_objects(storage: FilesystemStorage) -> None:
    result = storage.put(io.BytesIO(CONTENT))
    missing = hashlib.sha256(b"missing").hexdigest()

    assert storage.exists(result.key) is True
    assert storage.exists(f"sha256/{missing[:2]}/{missing[2:4]}/{missing}") is False


def test_get_missing_object_raises_not_found(storage: FilesystemStorage) -> None:
    missing = hashlib.sha256(b"missing").hexdigest()

    with pytest.raises(ObjectNotFoundError):
        storage.get(f"sha256/{missing[:2]}/{missing[2:4]}/{missing}")


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "sha256/../../" + "a" * 64,
        "sha256/aa/aa/" + "A" * 64,
        "sha256/bb/aa/" + "a" * 64,
        "md5/aa/aa/" + "a" * 64,
        "/sha256/aa/aa/" + "a" * 64,
        "sha256/aa/aa/" + "a" * 63,
    ],
)
def test_malformed_keys_are_rejected(storage: FilesystemStorage, key: str) -> None:
    with pytest.raises(InvalidKeyError):
        storage.get(key)
    with pytest.raises(InvalidKeyError):
        storage.exists(key)


def test_s3_driver_is_a_stub() -> None:
    driver = S3Storage(bucket="sketch2cad")
    key = "sha256/aa/aa/" + "a" * 64

    with pytest.raises(NotImplementedError):
        driver.put(io.BytesIO(CONTENT))
    with pytest.raises(NotImplementedError):
        driver.get(key)
    with pytest.raises(NotImplementedError):
        driver.exists(key)
