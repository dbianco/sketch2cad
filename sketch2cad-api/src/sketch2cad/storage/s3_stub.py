"""S3 driver placeholder; only the filesystem driver is implemented in the MVP."""

from sketch2cad.storage.base import ReadableStream, Storage, StoredObject

_MESSAGE = "The S3 storage driver is not implemented; use the filesystem driver"


class S3Storage(Storage):
    """Stub for a future S3 (Object Lock) driver; every operation raises."""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    def put(self, stream: ReadableStream) -> StoredObject:
        raise NotImplementedError(_MESSAGE)

    def get(self, key: str) -> bytes:
        raise NotImplementedError(_MESSAGE)

    def exists(self, key: str) -> bool:
        raise NotImplementedError(_MESSAGE)
