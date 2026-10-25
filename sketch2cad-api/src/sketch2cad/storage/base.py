"""Content-addressed storage interface shared by every driver."""

import re
from abc import ABC, abstractmethod
from typing import BinaryIO, NamedTuple, Protocol

HASH_ALGORITHM = "sha256"
"""Hash algorithm that names every object; it is also the first segment of each key."""

CHUNK_SIZE = 1024 * 1024
"""Bytes read from the input stream per iteration while hashing and writing."""

_KEY_PATTERN = re.compile(r"^sha256/([0-9a-f]{2})/([0-9a-f]{2})/([0-9a-f]{64})$")


class ReadableStream(Protocol):
    """Minimal binary stream accepted by ``Storage.put`` (files, ``BytesIO``, uploads)."""

    def read(self, size: int = -1, /) -> bytes: ...


class StoredObject(NamedTuple):
    """Result of ``Storage.put``: the content hash and the key that retrieves it."""

    sha256: str
    key: str


class StorageError(Exception):
    """Base class for storage failures."""


class InvalidKeyError(StorageError, ValueError):
    """The key does not have the form ``sha256/ab/cd/<hash>``."""


class ObjectNotFoundError(StorageError, KeyError):
    """No object is stored under the requested key."""


def key_for_hash(sha256: str) -> str:
    """Return the key ``sha256/<h[0:2]>/<h[2:4]>/<h>`` for a lowercase hex SHA-256."""
    key = f"{HASH_ALGORITHM}/{sha256[0:2]}/{sha256[2:4]}/{sha256}"
    validate_key(key)
    return key


def validate_key(key: str) -> str:
    """Return the hash named by ``key``; raise ``InvalidKeyError`` if the key is malformed.

    Only canonical keys are accepted, so a key can never escape the object tree.
    """
    match = _KEY_PATTERN.fullmatch(key)
    if match is None:
        raise InvalidKeyError(f"Invalid storage key: {key!r}")
    first, second, digest = match.groups()
    if digest[0:2] != first or digest[2:4] != second:
        raise InvalidKeyError(f"Storage key prefix does not match its hash: {key!r}")
    return digest


class Storage(ABC):
    """Write-once, content-addressed object store.

    Objects are named by the SHA-256 of their bytes. Storing the same bytes again returns
    the same key and never replaces the existing object. There is no update or delete
    operation: immutability is enforced at application level.
    """

    @abstractmethod
    def put(self, stream: ReadableStream) -> StoredObject:
        """Store the bytes read from ``stream`` until EOF and return their hash and key.

        If ``stream`` raises, the exception propagates and nothing is stored.
        """

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the bytes stored under ``key``; raise ``ObjectNotFoundError`` if absent."""

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Open the object under ``key`` for streaming reads; raise ``ObjectNotFoundError``.

        The caller must close the returned stream.
        """

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return whether an object is stored under ``key``."""
