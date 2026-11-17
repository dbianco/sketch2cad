"""Filesystem driver: objects live under ``<root>/objects/sha256/ab/cd/<hash>``."""

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from sketch2cad.storage.base import (
    CHUNK_SIZE,
    ObjectNotFoundError,
    ReadableStream,
    Storage,
    StoredObject,
    key_for_hash,
    validate_key,
)

logger = logging.getLogger(__name__)

OBJECT_MODE = 0o444
"""Stored objects are read-only for everyone."""

OBJECTS_DIR = "objects"
TMP_DIR = "tmp"


class FilesystemStorage(Storage):
    """Content-addressed storage on a local directory.

    Uploads are first written to ``<root>/tmp`` (outside the object tree) while being
    hashed, then published with a hard link, which fails instead of replacing an existing
    object. An interrupted or failed upload therefore never leaves a file under
    ``<root>/objects``.

    Threat model: this protects against overwrites through the application only. Anyone
    with access to the volume can still alter objects; that is detected by re-hashing, not
    prevented.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        self._objects = self._root / OBJECTS_DIR
        self._tmp = self._root / TMP_DIR
        self._objects.mkdir(parents=True, exist_ok=True)
        self._tmp.mkdir(parents=True, exist_ok=True)

    @property
    def objects_dir(self) -> Path:
        """Directory that holds the stored objects."""
        return self._objects

    def put(self, stream: ReadableStream) -> StoredObject:
        digest = hashlib.sha256()
        fd, tmp_name = tempfile.mkstemp(dir=self._tmp, prefix="upload-")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                while chunk := stream.read(CHUNK_SIZE):
                    digest.update(chunk)
                    tmp_file.write(chunk)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.chmod(tmp_path, OBJECT_MODE)

            sha256 = digest.hexdigest()
            key = key_for_hash(sha256)
            self._publish(tmp_path, self._path_for(key))
        finally:
            tmp_path.unlink(missing_ok=True)
        return StoredObject(sha256=sha256, key=key)

    def get(self, key: str) -> bytes:
        path = self._path_for(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc

    def open(self, key: str) -> BinaryIO:
        path = self._path_for(key)
        try:
            return path.open("rb")
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    def _path_for(self, key: str) -> Path:
        validate_key(key)
        return self._objects.joinpath(*key.split("/"))

    def _publish(self, tmp_path: Path, final_path: Path) -> None:
        """Link ``tmp_path`` to ``final_path`` unless an object already exists there."""
        if final_path.exists():
            logger.debug("Object %s already stored; keeping the existing file", final_path.name)
            return
        final_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(tmp_path, final_path)
        except FileExistsError:
            # A concurrent put of the same bytes won the race; its object is identical.
            logger.debug(
                "Object %s stored concurrently; keeping the existing file", final_path.name
            )
            return
        _fsync_dir(final_path.parent)
        logger.info("Stored object %s", final_path.name)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
