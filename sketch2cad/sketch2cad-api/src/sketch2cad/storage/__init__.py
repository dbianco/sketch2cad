"""Content-addressed, write-once object storage behind the ``Storage`` interface."""

from sketch2cad.storage.base import (
    InvalidKeyError,
    ObjectNotFoundError,
    ReadableStream,
    Storage,
    StorageError,
    StoredObject,
    key_for_hash,
    validate_key,
)
from sketch2cad.storage.filesystem import FilesystemStorage
from sketch2cad.storage.s3_stub import S3Storage

__all__ = [
    "FilesystemStorage",
    "InvalidKeyError",
    "ObjectNotFoundError",
    "ReadableStream",
    "S3Storage",
    "Storage",
    "StorageError",
    "StoredObject",
    "key_for_hash",
    "validate_key",
]
