"""Streaming ``multipart/form-data`` reader that enforces the file size cap as bytes arrive.

The file part is hashed and spooled while the body streams in, so an oversized upload is
rejected as soon as it crosses the limit instead of after the whole body was received.
"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING

from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

if TYPE_CHECKING:
    from python_multipart.multipart import MultipartCallbacks

FILE_FIELD = "file"
SPOOL_MAX_BYTES = 1024 * 1024
"""Bytes kept in memory before spilling to a temporary file (Starlette's UploadFile value)."""
MAX_FIELD_BYTES = 1024 * 1024
"""Size cap of each non-file field (Starlette's default ``max_part_size``)."""


class UploadError(Exception):
    """The request body is not an acceptable upload."""


class MalformedUploadError(UploadError):
    """The body is not valid multipart or lacks the single ``file`` part."""


class UploadTooLargeError(UploadError):
    """The file part exceeded the size cap."""


@dataclass
class ReceivedUpload:
    """The spooled file part, its SHA-256 and size, and the text fields of the form."""

    file: SpooledTemporaryFile[bytes]
    filename: str
    byte_size: int
    sha256: str
    fields: dict[str, str] = field(default_factory=dict[str, str])

    def close(self) -> None:
        self.file.close()


class _Collector:
    """Parser callbacks: spool and hash the file part, buffer the small text fields."""

    def __init__(self, max_file_bytes: int) -> None:
        self.max_file_bytes = max_file_bytes
        self.file: SpooledTemporaryFile[bytes] = SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES)
        self.digest = hashlib.sha256()
        self.size = 0
        self.filename: str | None = None
        self.fields: dict[str, str] = {}
        self.ended = False
        self._reset_part()

    def _reset_part(self) -> None:
        self._headers: dict[bytes, bytes] = {}
        self._header_field = bytearray()
        self._header_value = bytearray()
        self._name: str | None = None
        self._in_file = False
        self._value = bytearray()

    def callbacks(self) -> "MultipartCallbacks":
        return {
            "on_part_begin": self._reset_part,
            "on_header_field": lambda data, start, end: self._header_field.extend(data[start:end]),
            "on_header_value": lambda data, start, end: self._header_value.extend(data[start:end]),
            "on_header_end": self._on_header_end,
            "on_headers_finished": self._on_headers_finished,
            "on_part_data": self._on_part_data,
            "on_part_end": self._on_part_end,
            "on_end": self._on_end,
        }

    def _on_header_end(self) -> None:
        self._headers[bytes(self._header_field).lower()] = bytes(self._header_value)
        self._header_field = bytearray()
        self._header_value = bytearray()

    def _on_headers_finished(self) -> None:
        disposition = self._headers.get(b"content-disposition", b"").decode("latin-1")
        _, options = parse_options_header(disposition)
        name = options.get(b"name")
        if name is None:
            raise MalformedUploadError("multipart part without a name")
        self._name = name.decode("latin-1")
        if self._name == FILE_FIELD:
            if self.filename is not None:
                raise MalformedUploadError("more than one file part")
            raw = options.get(b"filename", b"").decode("utf-8", errors="replace")
            self.filename = raw.replace("\x00", "").replace("\\", "/").rsplit("/", 1)[-1]
            self._in_file = True

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self._in_file:
            self.size += len(chunk)
            if self.size > self.max_file_bytes:
                raise UploadTooLargeError("file part exceeds the size cap")
            self.digest.update(chunk)
            self.file.write(chunk)
            return
        self._value.extend(chunk)
        if len(self._value) > MAX_FIELD_BYTES:
            raise MalformedUploadError("form field exceeds the size cap")

    def _on_part_end(self) -> None:
        if not self._in_file and self._name is not None:
            try:
                self.fields[self._name] = self._value.decode("utf-8")
            except UnicodeDecodeError:
                raise MalformedUploadError("form field is not UTF-8") from None
        self._reset_part()

    def _on_end(self) -> None:
        self.ended = True


async def read_upload(
    content_type: str | None, body: AsyncIterator[bytes], *, max_file_bytes: int
) -> ReceivedUpload:
    """Parse a multipart ``body`` holding one ``file`` part of at most ``max_file_bytes``.

    Raises :class:`UploadTooLargeError` as soon as the file part crosses the cap and
    :class:`MalformedUploadError` for any other defect; the spool is closed on failure.
    """
    media_type, params = parse_options_header(content_type)
    boundary = params.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise MalformedUploadError("the body is not multipart/form-data")
    collector = _Collector(max_file_bytes)
    try:
        parser = MultipartParser(boundary, callbacks=collector.callbacks())
        async for chunk in body:
            parser.write(chunk)
        parser.finalize()
        if not collector.ended or collector.filename is None:
            raise MalformedUploadError("the body is incomplete or has no file part")
    except MultipartParseError:
        collector.file.close()
        raise MalformedUploadError("the multipart body is malformed") from None
    except BaseException:
        collector.file.close()
        raise
    collector.file.seek(0)
    return ReceivedUpload(
        file=collector.file,
        filename=collector.filename,
        byte_size=collector.size,
        sha256=collector.digest.hexdigest(),
        fields=collector.fields,
    )
