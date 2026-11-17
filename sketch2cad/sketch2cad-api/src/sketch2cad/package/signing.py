"""Ed25519 signing key handling and the ``ed25519-jcs-v1`` manifest signature (spec 14.2).

Scheme ``ed25519-jcs-v1``: the signature is Ed25519 over the RFC 8785 (JCS) bytes of the
manifest without its ``signature`` field. The ``signature`` field holds ``scheme``,
``public_key_id`` and ``value`` (standard base64 of the 64 signature bytes). The key id is
``ed25519:`` plus the first 16 hex digits of SHA-256 over the 32 raw public key bytes, so
a verifier can check that the public key it was given is the one the manifest names.

Key file: an unencrypted PKCS#8 PEM Ed25519 private key at ``SIGNING_KEY_PATH``, mode 0600
or stricter (only owner read/write bits: 0400 is accepted, 0640 or 0700 are not). The API
and the worker refuse to start when the file is missing, is not a regular file, has any
other mode bit or is not an Ed25519 key (jotdown H4-11, spec section 15). Error messages
name the path and never the key bytes.

This module depends only on the standard library, ``cryptography`` and ``rfc8785``, so the
standalone ``sketch2cad-verify`` can reuse it without the application or a database.
"""

import base64
import binascii
import hashlib
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SCHEME = "ed25519-jcs-v1"
SIGNATURE_FIELD = "signature"
KEY_ID_PREFIX = "ed25519:"
KEY_ID_HEX_DIGITS = 16
SIGNATURE_BYTES = 64
KEY_FILE_MODE = 0o600
KEY_DIR_MODE = 0o700
FORBIDDEN_MODE_BITS = 0o7777 & ~KEY_FILE_MODE
"""Every mode bit outside owner read/write (group, other, execute, setuid, sticky)."""
SIGNING_KEY_ENV = "SIGNING_KEY_PATH"


class SigningKeyError(Exception):
    """The signing key file is missing, unsafe or unreadable; the message names the path."""


class InvalidReason(StrEnum):
    """Why a manifest signature does not verify."""

    MISSING_SIGNATURE = "missing_signature"
    UNKNOWN_SCHEME = "unknown_scheme"
    KEY_ID_MISMATCH = "key_id_mismatch"
    MALFORMED_SIGNATURE = "malformed_signature"
    NOT_CANONICALIZABLE = "not_canonicalizable"
    BAD_SIGNATURE = "bad_signature"


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of :func:`verify_manifest`; ``reason`` and ``detail`` are ``None`` when valid."""

    valid: bool
    reason: InvalidReason | None = None
    detail: str | None = None


# --- Public keys -------------------------------------------------------------------------


def public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    """The 32 raw bytes of ``public_key``."""
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def encode_public_key(public_key: Ed25519PublicKey) -> str:
    """Standard base64 of the raw public key bytes."""
    return base64.b64encode(public_key_bytes(public_key)).decode("ascii")


def public_key_pem(public_key: Ed25519PublicKey) -> bytes:
    """SubjectPublicKeyInfo PEM of ``public_key`` (the file shipped in the package)."""
    return public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def load_public_key(data: bytes | str) -> Ed25519PublicKey:
    """Parse a public key given as SubjectPublicKeyInfo PEM or base64 of the raw bytes.

    Raises ``ValueError`` when ``data`` is neither or is not an Ed25519 key.
    """
    raw = data.encode("ascii") if isinstance(data, str) else data
    if raw.lstrip().startswith(b"-----BEGIN PUBLIC KEY-----"):
        key = serialization.load_pem_public_key(raw)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("the public key is not an Ed25519 key")
        return key
    try:
        decoded = base64.b64decode(raw.strip(), validate=True)
    except binascii.Error:
        raise ValueError("the public key is neither PEM nor base64") from None
    if len(decoded) != 32:
        raise ValueError("a raw Ed25519 public key has 32 bytes")
    return Ed25519PublicKey.from_public_bytes(decoded)


def public_key_id(public_key: Ed25519PublicKey) -> str:
    """``ed25519:<first 16 hex digits of SHA-256(raw public key)>``."""
    digest = hashlib.sha256(public_key_bytes(public_key)).hexdigest()
    return KEY_ID_PREFIX + digest[:KEY_ID_HEX_DIGITS]


# --- Key file ----------------------------------------------------------------------------


def _private_key_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def generate_signing_key(path: str | os.PathLike[str]) -> Ed25519PrivateKey:
    """Create a new Ed25519 key file at ``path`` with mode 0600 and return the key.

    Missing parent directories are created with mode 0700. The file is written to a
    private temporary file in the same directory and published with a hard link, so the
    key appears complete or not at all, and an existing file is never overwritten
    (``SigningKeyError``).
    """
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise SigningKeyError(f"signing key file {target} already exists; refusing to overwrite")
    target.parent.mkdir(mode=KEY_DIR_MODE, parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    # mkstemp creates the file with O_EXCL and mode 0600.
    fd, tmp_name = tempfile.mkstemp(prefix=".signing-key-", dir=target.parent)
    try:
        os.fchmod(fd, KEY_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(_private_key_pem(key))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp_name, target)
    except FileExistsError:
        raise SigningKeyError(
            f"signing key file {target} already exists; refusing to overwrite"
        ) from None
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return key


def check_key_file(path: str | os.PathLike[str]) -> None:
    """Raise ``SigningKeyError`` unless ``path`` is a regular file with mode 0600 or stricter."""
    target = Path(path)
    try:
        info = target.stat()
    except FileNotFoundError:
        raise SigningKeyError(f"signing key file {target} does not exist") from None
    except OSError as exc:
        raise SigningKeyError(
            f"signing key file {target} cannot be read ({exc.strerror or type(exc).__name__})"
        ) from None
    if not stat.S_ISREG(info.st_mode):
        raise SigningKeyError(f"signing key file {target} is not a regular file")
    mode = stat.S_IMODE(info.st_mode)
    if mode & FORBIDDEN_MODE_BITS:
        raise SigningKeyError(
            f"signing key file {target} has mode {mode:04o}; it must be 0600 or stricter "
            "(owner read/write only): run chmod 600 on it"
        )


def load_signing_key(path: str | os.PathLike[str]) -> Ed25519PrivateKey:
    """Check the key file at ``path`` (:func:`check_key_file`) and load its Ed25519 key."""
    check_key_file(path)
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise SigningKeyError(
            f"signing key file {target} cannot be read ({exc.strerror or type(exc).__name__})"
        ) from None
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError):
        # The parser message is not echoed: it is about the key contents.
        raise SigningKeyError(
            f"signing key file {target} is not an unencrypted PEM private key"
        ) from None
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningKeyError(f"signing key file {target} does not hold an Ed25519 key")
    return key


def require_signing_key(environ: Mapping[str, str] | None = None) -> Ed25519PrivateKey:
    """Startup check of the API and the worker: load the key at ``SIGNING_KEY_PATH``.

    Raises ``SystemExit`` with a one-line message naming the path (exit status 1, no
    traceback) when the variable is unset or the key file fails :func:`load_signing_key`.
    """
    env = os.environ if environ is None else environ
    path = env.get(SIGNING_KEY_ENV, "")
    if not path:
        raise SystemExit(f"error: {SIGNING_KEY_ENV} is not set; refusing to start")
    try:
        return load_signing_key(path)
    except SigningKeyError as exc:
        raise SystemExit(f"error: {exc}; refusing to start") from None


# --- Signature ---------------------------------------------------------------------------


def signing_payload(manifest: Mapping[str, Any]) -> bytes:
    """RFC 8785 bytes of ``manifest`` without its ``signature`` field: the signed message."""
    unsigned = {name: value for name, value in manifest.items() if name != SIGNATURE_FIELD}
    return rfc8785.dumps(unsigned)


def sign_manifest(manifest: Mapping[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    """A copy of ``manifest`` with its ``signature`` field set (any previous one replaced).

    Ed25519 is deterministic: the same manifest and key always give the same signature.
    """
    value = key.sign(signing_payload(manifest))
    signed = {name: item for name, item in manifest.items() if name != SIGNATURE_FIELD}
    signed[SIGNATURE_FIELD] = {
        "scheme": SCHEME,
        "public_key_id": public_key_id(key.public_key()),
        "value": base64.b64encode(value).decode("ascii"),
    }
    return signed


def _decode_signature(signature: Mapping[str, Any]) -> bytes:
    value = signature.get("value")
    if not isinstance(value, str):
        raise ValueError("signature.value is not a string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except binascii.Error:
        raise ValueError("signature.value is not base64") from None
    if len(decoded) != SIGNATURE_BYTES:
        raise ValueError(f"signature.value does not decode to {SIGNATURE_BYTES} bytes")
    return decoded


def verify_manifest(manifest: Mapping[str, Any], public_key: Ed25519PublicKey) -> VerifyResult:
    """Verify the ``ed25519-jcs-v1`` signature of ``manifest`` against ``public_key``.

    The public key must come from outside the manifest (the package's public key file or
    the operator); the manifest only names it through ``public_key_id``, which must match.
    """
    signature = manifest.get(SIGNATURE_FIELD)
    if not isinstance(signature, Mapping):
        return VerifyResult(False, InvalidReason.MISSING_SIGNATURE, "the manifest is not signed")
    fields: Mapping[str, Any] = signature  # pyright: ignore[reportUnknownVariableType]
    if fields.get("scheme") != SCHEME:
        return VerifyResult(
            False, InvalidReason.UNKNOWN_SCHEME, f"signature.scheme is not {SCHEME!r}"
        )
    if fields.get("public_key_id") != public_key_id(public_key):
        return VerifyResult(
            False,
            InvalidReason.KEY_ID_MISMATCH,
            "signature.public_key_id does not match the given public key",
        )
    try:
        value = _decode_signature(fields)
    except ValueError as exc:
        return VerifyResult(False, InvalidReason.MALFORMED_SIGNATURE, str(exc))
    try:
        payload = signing_payload(manifest)
    except rfc8785.CanonicalizationError as exc:
        return VerifyResult(False, InvalidReason.NOT_CANONICALIZABLE, str(exc))
    try:
        public_key.verify(value, payload)
    except InvalidSignature:
        return VerifyResult(
            False, InvalidReason.BAD_SIGNATURE, "the signature does not match the manifest"
        )
    return VerifyResult(True)
