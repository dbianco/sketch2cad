#!/usr/bin/env python3
"""sketch2cad-verify: offline verifier of a Sketch2CAD final package (spec 14, REQ-14, US-17).

Usage::

    python sketch2cad_verify.py PACKAGE.zip [--public-key FILE.pem] [--key-id ID] [--json]
                                            [--quiet]

Checks, in this order, each reported as an OK/FAIL line (output in Spanish):

1. The ZIP is readable and safe: plain unique member names (no paths, no ``..``, no
   absolute or drive names), no encryption, at most :data:`MAX_MEMBERS` members and
   :data:`MAX_TOTAL_BYTES` uncompressed bytes in total, ``manifest.json`` present.
2. ``manifest.json`` is strict JSON whose bytes equal its RFC 8785 (JCS) form, with
   ``manifest_version`` 2.0 and the fields the next checks read.
3. The ``ed25519-jcs-v1`` signature over JCS(manifest without ``signature``) verifies with
   the public key (``--public-key`` or the package's ``public_key.pem``) and the key id.
4. Every artifact of the manifest is in the ZIP with its SHA-256; no other member exists
   besides ``manifest.json``, ``VERIFY.md`` and ``public_key.pem``.
5. ``snapshot.json`` hashes to ``revision.snapshot_sha256`` and names the manifest's plan.
6. ``events.json`` is a valid hash chain of ``case_file.id`` whose count and last hash
   equal ``events_chain`` (so a truncated tail is detected).

Exit status: 0 when every check passes, 1 when any fails (the failing artifact or event is
named), 2 on usage errors. The package is read in memory: no network, no temporary files.

Self-contained on purpose: it depends only on the standard library, ``cryptography`` and
``rfc8785`` and imports nothing from the ``sketch2cad`` application. The chain check is a
copy of ``sketch2cad/audit/verify.py`` (``verify_chain`` and helpers) and the signature
check a copy of ``sketch2cad/package/signing.py`` (``verify_manifest`` and helpers);
``tests/package/test_verifier.py`` runs both implementations on the same inputs to catch
divergence. Keep them in sync.
"""

import argparse
import base64
import binascii
import hashlib
import io
import json
import re
import sys
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, cast

import rfc8785
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VERSION = "1.0"
MANIFEST_FILENAME = "manifest.json"
VERIFY_FILENAME = "VERIFY.md"
PUBLIC_KEY_FILENAME = "public_key.pem"
UNHASHED_MEMBERS = frozenset({MANIFEST_FILENAME, VERIFY_FILENAME, PUBLIC_KEY_FILENAME})
MANIFEST_VERSION = "2.0"
MAX_MEMBERS = 64
MAX_TOTAL_BYTES = 500 * 1024 * 1024
"""Zip-bomb guard: the package file and the sum of uncompressed members are capped here."""
MAX_KEY_FILE_BYTES = 64 * 1024
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
KEY_ID_PATTERN = re.compile(r"^ed25519:[0-9a-f]{16}$")
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2

# --- Copy of sketch2cad/audit/verify.py (hash chain over plain event dictionaries) ------

CONTENT_FIELDS = (
    "id",
    "case_file_id",
    "plan_id",
    "revision_id",
    "actor_id",
    "actor_role",
    "kind",
    "entity_id",
    "before",
    "after",
    "reason",
    "client_ip",
    "occurred_at",
    "prev_hash",
)
EVENT_FIELDS = frozenset((*CONTENT_FIELDS, "hash"))
AUTH_CHAIN_SEED = b"auth"


class InvalidReason(StrEnum):
    """Why an event breaks the chain."""

    MALFORMED = "malformed"
    CASE_FILE_MISMATCH = "case_file_mismatch"
    ID_NOT_INCREASING = "id_not_increasing"
    PREV_HASH_MISMATCH = "prev_hash_mismatch"
    NOT_CANONICALIZABLE = "not_canonicalizable"
    HASH_MISMATCH = "hash_mismatch"


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of :func:`verify_chain`; failure fields are ``None`` when valid."""

    valid: bool
    checked: int
    first_invalid_id: Any = None
    first_invalid_index: int | None = None
    reason: InvalidReason | None = None
    detail: str | None = None


class ChainError(Exception):
    """Internal signal: the current event is invalid."""

    def __init__(self, reason: InvalidReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def genesis_hash_of(case_file_id: str | None) -> str:
    """``prev_hash`` of the first event: SHA-256 of the case id, or of ``b"auth"``."""
    if case_file_id is None:
        return sha256_hex(AUTH_CHAIN_SEED)
    return sha256_hex(case_file_id.encode("ascii"))


def recompute_hash(event: Mapping[str, Any]) -> str:
    """``SHA-256(prev_hash || JCS(content))`` of an event dictionary."""
    content = {name: event[name] for name in CONTENT_FIELDS}
    return sha256_hex(event["prev_hash"].encode("ascii") + rfc8785.dumps(content))


def check_shape(event: Mapping[str, Any]) -> None:
    """Exact field set and the types the link checks rely on."""
    fields = set(event.keys())
    missing = EVENT_FIELDS - fields
    if missing:
        raise ChainError(InvalidReason.MALFORMED, f"missing fields: {sorted(missing)}")
    unexpected = fields - EVENT_FIELDS
    if unexpected:
        raise ChainError(InvalidReason.MALFORMED, f"unexpected fields: {sorted(unexpected)}")
    event_id = event["id"]
    if not isinstance(event_id, int) or isinstance(event_id, bool):
        raise ChainError(InvalidReason.MALFORMED, "id is not an integer")
    if event["case_file_id"] is not None and not isinstance(event["case_file_id"], str):
        raise ChainError(InvalidReason.MALFORMED, "case_file_id is not a string or null")
    for name in ("prev_hash", "hash"):
        if not isinstance(event[name], str):
            raise ChainError(InvalidReason.MALFORMED, f"{name} is not a string")


def check_event(
    event: Mapping[str, Any], case_file_id: str | None, previous_id: int | None, expected_prev: str
) -> None:
    """Raise :class:`ChainError` when ``event`` does not continue the chain."""
    check_shape(event)
    if event["case_file_id"] != case_file_id:
        raise ChainError(InvalidReason.CASE_FILE_MISMATCH, "event belongs to another case file")
    if previous_id is not None and event["id"] <= previous_id:
        raise ChainError(InvalidReason.ID_NOT_INCREASING, "id does not follow the previous id")
    if event["prev_hash"] != expected_prev:
        raise ChainError(
            InvalidReason.PREV_HASH_MISMATCH, "prev_hash does not match the previous hash"
        )
    try:
        recomputed = recompute_hash(event)
    except (rfc8785.CanonicalizationError, UnicodeEncodeError) as exc:
        raise ChainError(InvalidReason.NOT_CANONICALIZABLE, str(exc)) from exc
    if recomputed != event["hash"]:
        raise ChainError(InvalidReason.HASH_MISMATCH, "stored hash does not match the content")


def verify_chain(events: Sequence[Mapping[str, Any]]) -> VerifyResult:
    """Verify the hash chain of one case file (or the auth chain), events oldest first."""
    case_file_id: str | None = None
    previous_id: int | None = None
    expected_prev = ""
    for index, event in enumerate(events):
        try:
            if index == 0:
                check_shape(event)
                case_file_id = event["case_file_id"]
                try:
                    expected_prev = genesis_hash_of(case_file_id)
                except UnicodeEncodeError as exc:
                    raise ChainError(InvalidReason.MALFORMED, "case_file_id is not ASCII") from exc
            check_event(event, case_file_id, previous_id, expected_prev)
        except ChainError as error:
            return VerifyResult(
                valid=False,
                checked=index,
                first_invalid_id=event.get("id"),
                first_invalid_index=index,
                reason=error.reason,
                detail=error.detail,
            )
        previous_id = event["id"]
        expected_prev = event["hash"]
    return VerifyResult(valid=True, checked=len(events))


# --- Copy of sketch2cad/package/signing.py (ed25519-jcs-v1 verification) ----------------

SCHEME = "ed25519-jcs-v1"
SIGNATURE_FIELD = "signature"
KEY_ID_PREFIX = "ed25519:"
KEY_ID_HEX_DIGITS = 16
SIGNATURE_BYTES = 64


class SignatureReason(StrEnum):
    """Why a manifest signature does not verify (``InvalidReason`` of signing.py)."""

    MISSING_SIGNATURE = "missing_signature"
    UNKNOWN_SCHEME = "unknown_scheme"
    KEY_ID_MISMATCH = "key_id_mismatch"
    MALFORMED_SIGNATURE = "malformed_signature"
    NOT_CANONICALIZABLE = "not_canonicalizable"
    BAD_SIGNATURE = "bad_signature"


@dataclass(frozen=True)
class SignatureResult:
    """Outcome of :func:`verify_manifest`; ``reason`` and ``detail`` are ``None`` when valid."""

    valid: bool
    reason: SignatureReason | None = None
    detail: str | None = None


def load_public_key(data: bytes | str) -> Ed25519PublicKey:
    """Parse SubjectPublicKeyInfo PEM or base64 of the raw key; ``ValueError`` otherwise."""
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
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return KEY_ID_PREFIX + hashlib.sha256(raw).hexdigest()[:KEY_ID_HEX_DIGITS]


def signing_payload(manifest: Mapping[str, Any]) -> bytes:
    """RFC 8785 bytes of ``manifest`` without its ``signature`` field: the signed message."""
    unsigned = {name: value for name, value in manifest.items() if name != SIGNATURE_FIELD}
    return rfc8785.dumps(unsigned)


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


def verify_manifest(manifest: Mapping[str, Any], public_key: Ed25519PublicKey) -> SignatureResult:
    """Verify the ``ed25519-jcs-v1`` signature of ``manifest`` against ``public_key``."""
    signature = manifest.get(SIGNATURE_FIELD)
    if not isinstance(signature, Mapping):
        return SignatureResult(
            False, SignatureReason.MISSING_SIGNATURE, "the manifest is not signed"
        )
    fields: Mapping[str, Any] = signature  # pyright: ignore[reportUnknownVariableType]
    if fields.get("scheme") != SCHEME:
        return SignatureResult(
            False, SignatureReason.UNKNOWN_SCHEME, f"signature.scheme is not {SCHEME!r}"
        )
    if fields.get("public_key_id") != public_key_id(public_key):
        return SignatureResult(
            False,
            SignatureReason.KEY_ID_MISMATCH,
            "signature.public_key_id does not match the given public key",
        )
    try:
        value = _decode_signature(fields)
    except ValueError as exc:
        return SignatureResult(False, SignatureReason.MALFORMED_SIGNATURE, str(exc))
    try:
        payload = signing_payload(manifest)
    except rfc8785.CanonicalizationError as exc:
        return SignatureResult(False, SignatureReason.NOT_CANONICALIZABLE, str(exc))
    try:
        public_key.verify(value, payload)
    except InvalidSignature:
        return SignatureResult(
            False, SignatureReason.BAD_SIGNATURE, "the signature does not match the manifest"
        )
    return SignatureResult(True)


# --- Report ------------------------------------------------------------------------------

CHAIN_REASONS = {
    InvalidReason.MALFORMED: "evento mal formado",
    InvalidReason.CASE_FILE_MISMATCH: "pertenece a otro expediente",
    InvalidReason.ID_NOT_INCREASING: "id no creciente (orden alterado)",
    InvalidReason.PREV_HASH_MISMATCH: "prev_hash no enlaza con el evento anterior",
    InvalidReason.NOT_CANONICALIZABLE: "contenido no canonicalizable (RFC 8785)",
    InvalidReason.HASH_MISMATCH: "el hash no coincide con el contenido",
}
SIGNATURE_REASONS = {
    SignatureReason.MISSING_SIGNATURE: "el manifiesto no está firmado",
    SignatureReason.UNKNOWN_SCHEME: f"esquema de firma distinto de {SCHEME}",
    SignatureReason.KEY_ID_MISMATCH: "signature.public_key_id no corresponde a la clave",
    SignatureReason.MALFORMED_SIGNATURE: "valor de firma mal formado",
    SignatureReason.NOT_CANONICALIZABLE: "manifiesto no canonicalizable (RFC 8785)",
    SignatureReason.BAD_SIGNATURE: "la firma no corresponde al manifiesto (alterado)",
}
PROVENANCE_NOTICE = (
    "AVISO: se usa la clave pública incluida en el paquete (public_key.pem). Eso solo prueba "
    "la consistencia interna: quien altere el paquete puede sustituir la clave y volver a "
    "firmar. Para probar el origen, pase --public-key con la clave publicada por la "
    "institución o --key-id ed25519:... con el identificador publicado."
)


class UsageError(Exception):
    """Bad command-line input: exit status 2."""


@dataclass
class Check:
    """One numbered check: ``ok``, ``fail`` or ``skipped`` with notes and failures."""

    number: int
    title: str
    status: str = "ok"
    notes: list[str] = field(default_factory=list[str])
    failures: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def fail(self, target: str, message: str) -> None:
        self.status = "fail"
        self.failures.append((target, message))


@dataclass
class Package:
    """State shared by the checks."""

    members: dict[str, bytes] = field(default_factory=dict[str, bytes])
    manifest: dict[str, Any] = field(default_factory=dict[str, Any])
    artifacts: list[dict[str, str]] = field(default_factory=list[dict[str, str]])


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"clave duplicada {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError(f"constante JSON no permitida {name}")


def parse_json(data: bytes) -> Any:
    """Strict JSON: UTF-8, no duplicate keys, no NaN/Infinity; ``ValueError`` otherwise."""
    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except RecursionError:
        raise ValueError("JSON demasiado anidado") from None


def is_safe_name(name: str) -> bool:
    """A plain file name: no directories, absolute paths, ``..``, drives or control bytes."""
    if not name or name in {".", ".."} or "\\" in name or ":" in name:
        return False
    if any(ord(char) < 32 for char in name):
        return False
    return PurePosixPath(name).name == name


def _short(digest: str) -> str:
    return digest[:16] + "…"


# --- Checks ------------------------------------------------------------------------------


def check_zip(check: Check, data: bytes, package: Package) -> bool:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, EOFError) as exc:
        check.fail("ZIP", f"no es un ZIP legible ({exc})")
        return False
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            check.fail("ZIP", f"{len(infos)} miembros, más del límite de {MAX_MEMBERS}")
            return False
        total = sum(info.file_size for info in infos)
        if total > MAX_TOTAL_BYTES:
            check.fail("ZIP", f"{total} bytes sin comprimir, más del límite de {MAX_TOTAL_BYTES}")
            return False
        seen: set[str] = set()
        for info in infos:
            name = info.filename
            if not is_safe_name(name):
                check.fail(name, "nombre de miembro inseguro (ruta, '..', absoluto o directorio)")
            elif name in seen:
                check.fail(name, "miembro duplicado")
            elif info.flag_bits & 0x1:
                check.fail(name, "miembro cifrado")
            elif info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                check.fail(name, f"método de compresión no admitido ({info.compress_type})")
            seen.add(name)
        if check.failures:
            return False
        for info in infos:
            try:
                with archive.open(info) as handle:
                    content = handle.read(info.file_size + 1)
            except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError) as exc:
                check.fail(info.filename, f"no se puede leer ({exc})")
                continue
            if len(content) != info.file_size:
                check.fail(info.filename, "el tamaño leído no coincide con el declarado")
            package.members[info.filename] = content
    if MANIFEST_FILENAME not in package.members:
        check.fail(MANIFEST_FILENAME, "falta en el ZIP")
    if check.failures:
        return False
    check.notes.append(f"{len(infos)} miembros, {total} bytes sin comprimir")
    return True


def as_object(value: Any) -> dict[str, Any] | None:
    """``value`` as a JSON object, or ``None`` when it is not one."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def as_object_list(value: Any) -> list[dict[str, Any]] | None:
    """``value`` as a list of JSON objects, or ``None`` when it is not one."""
    if not isinstance(value, list):
        return None
    items = [as_object(item) for item in cast(list[Any], value)]
    return [item for item in items if item is not None] if None not in items else None


def _manifest_shape(manifest: dict[str, Any]) -> str | None:
    """``None`` when ``manifest`` has the fields the checks read, else what is wrong."""
    case_file = as_object(manifest.get("case_file"))
    if case_file is None or not isinstance(case_file.get("id"), str):
        return "case_file.id falta o no es texto"
    if not isinstance(manifest.get("plan_id"), str):
        return "plan_id falta o no es texto"
    revision = as_object(manifest.get("revision"))
    if revision is None or not isinstance(revision.get("number"), int):
        return "revision.number falta o no es entero"
    if not SHA256_HEX.match(str(revision.get("snapshot_sha256"))):
        return "revision.snapshot_sha256 no es un SHA-256 hexadecimal"
    chain = as_object(manifest.get("events_chain"))
    if chain is None or not isinstance(chain.get("count"), int):
        return "events_chain.count falta o no es entero"
    if not SHA256_HEX.match(str(chain.get("last_hash"))):
        return "events_chain.last_hash no es un SHA-256 hexadecimal"
    artifacts = as_object_list(manifest.get("artifacts"))
    if not artifacts:
        return "artifacts falta, está vacío o no es una lista de objetos"
    for index, item in enumerate(artifacts):
        if not all(isinstance(item.get(name), str) for name in ("kind", "filename", "sha256")):
            return f"artifacts[{index}] no tiene kind, filename y sha256"
        if not is_safe_name(item["filename"]) or not SHA256_HEX.match(item["sha256"]):
            return f"artifacts[{index}] tiene un nombre o un SHA-256 no válido"
    return None


def check_manifest(check: Check, package: Package) -> bool:
    raw = package.members[MANIFEST_FILENAME]
    try:
        manifest = parse_json(raw)
    except ValueError as exc:
        check.fail(MANIFEST_FILENAME, f"no es JSON válido ({exc})")
        return False
    manifest = as_object(manifest)
    if manifest is None:
        check.fail(MANIFEST_FILENAME, "no es un objeto JSON")
        return False
    problem = _manifest_shape(manifest)
    if problem is not None:
        check.fail(MANIFEST_FILENAME, problem)
        return False
    try:
        canonical = rfc8785.dumps(manifest)
    except rfc8785.CanonicalizationError as exc:
        canonical = None
        check.fail(MANIFEST_FILENAME, f"no canonicalizable con RFC 8785 ({exc})")
    if canonical is not None and canonical != raw:
        check.fail(MANIFEST_FILENAME, "los bytes no están en forma canónica RFC 8785 (JCS)")
    version = manifest.get("manifest_version")
    if version != MANIFEST_VERSION:
        check.fail(MANIFEST_FILENAME, f"manifest_version {version!r}, se esperaba 2.0")
    package.manifest = manifest
    package.artifacts = manifest["artifacts"]
    if check.status == "ok":
        check.notes.append(f"JSON canónico RFC 8785, manifest_version {version}")
    return True


def check_signature(
    check: Check, package: Package, operator_key: Ed25519PublicKey | None, pinned_id: str | None
) -> None:
    embedded = package.members.get(PUBLIC_KEY_FILENAME)
    embedded_key: Ed25519PublicKey | None = None
    if embedded is not None:
        try:
            embedded_key = load_public_key(embedded)
        except (ValueError, UnsupportedAlgorithm):
            check.fail(PUBLIC_KEY_FILENAME, "no es una clave pública Ed25519 válida")
    elif operator_key is None:
        check.fail(PUBLIC_KEY_FILENAME, "falta en el ZIP y no se indicó --public-key")
    key = operator_key if operator_key is not None else embedded_key
    label = "--public-key" if operator_key is not None else PUBLIC_KEY_FILENAME
    if key is None:
        return
    key_id = public_key_id(key)
    if operator_key is not None and embedded_key is not None:
        if public_key_id(embedded_key) != key_id:
            check.fail(PUBLIC_KEY_FILENAME, f"la clave del paquete no es la indicada ({key_id})")
    if pinned_id is not None and key_id != pinned_id:
        check.fail(label, f"la clave tiene identificador {key_id}, distinto del fijado {pinned_id}")
    result = verify_manifest(package.manifest, key)
    if not result.valid and result.reason is not None:
        target = MANIFEST_FILENAME
        message = f"{SIGNATURE_REASONS[result.reason]} (código {result.reason.value})"
        if result.reason is SignatureReason.KEY_ID_MISMATCH:
            named = package.manifest[SIGNATURE_FIELD].get("public_key_id")
            message += f": el manifiesto nombra {named!r} y la clave de {label} es {key_id}"
            target = PUBLIC_KEY_FILENAME if operator_key is None else MANIFEST_FILENAME
        check.fail(target, message)
    if check.status == "ok":
        origin = "indicada con --public-key" if operator_key else "public_key.pem del paquete"
        pinned = ", identificador fijado con --key-id" if pinned_id else ""
        check.notes.append(f"{SCHEME} válida, clave {key_id} ({origin}{pinned})")


def check_artifacts(check: Check, package: Package) -> None:
    listed: set[str] = set()
    for item in package.artifacts:
        name = item["filename"]
        if name in listed or name in UNHASHED_MEMBERS:
            check.fail(name, "aparece repetido o reservado en artifacts del manifiesto")
            continue
        listed.add(name)
        data = package.members.get(name)
        if data is None:
            check.fail(name, f"artefacto {item['kind']} listado en el manifiesto y ausente del ZIP")
            continue
        digest = sha256_hex(data)
        if digest != item["sha256"]:
            check.fail(
                name,
                f"el SHA-256 no coincide (manifiesto {_short(item['sha256'])}, "
                f"calculado {_short(digest)})",
            )
    for name in package.members:
        if name not in listed and name not in UNHASHED_MEMBERS:
            check.fail(name, "archivo no listado en el manifiesto")
    if check.status == "ok":
        check.notes.append(f"{len(listed)} artefactos con su SHA-256")
    if VERIFY_FILENAME in package.members:
        check.notes.append("VERIFY.md no está cubierto por el manifiesto (informativo)")


def _artifact(package: Package, kind: str, default: str) -> tuple[str, bytes | None]:
    for item in package.artifacts:
        if item["kind"] == kind:
            return item["filename"], package.members.get(item["filename"])
    return default, None


def check_snapshot(check: Check, package: Package) -> None:
    name, data = _artifact(package, "snapshot", "snapshot.json")
    if data is None:
        check.fail(name, "falta la instantánea de la revisión")
        return
    expected = package.manifest["revision"]["snapshot_sha256"]
    if sha256_hex(data) != expected:
        check.fail(name, "el SHA-256 no coincide con revision.snapshot_sha256")
        return
    try:
        snapshot = as_object(parse_json(data))
    except ValueError as exc:
        check.fail(name, f"no es JSON válido ({exc})")
        return
    if snapshot is None:
        check.fail(name, "no es un objeto JSON")
        return
    if snapshot.get("plan_id") != package.manifest["plan_id"]:
        check.fail(name, "plan_id distinto del manifiesto")
    if snapshot.get("revision_number") != package.manifest["revision"]["number"]:
        check.fail(name, "revision_number distinto de revision.number del manifiesto")
    if check.status == "ok":
        check.notes.append(f"SHA-256 = revision.snapshot_sha256 ({_short(expected)})")


def check_events(check: Check, package: Package) -> None:
    name, data = _artifact(package, "events", "events.json")
    if data is None:
        check.fail(name, "falta la cadena de eventos")
        return
    try:
        chain = as_object_list(parse_json(data))
    except ValueError as exc:
        check.fail(name, f"no es JSON válido ({exc})")
        return
    if chain is None:
        check.fail(name, "no es una lista de eventos")
        return
    expected = package.manifest["events_chain"]
    if not chain:
        check.fail(name, "la cadena de eventos está vacía")
        return
    result = verify_chain(chain)
    if not result.valid and result.reason is not None:
        check.fail(
            f"{name}: evento id={result.first_invalid_id}",
            f"índice {result.first_invalid_index}: {CHAIN_REASONS[result.reason]} "
            f"(código {result.reason.value})",
        )
    case_file_id = package.manifest["case_file"]["id"]
    if chain[0].get("case_file_id") != case_file_id:
        check.fail(
            f"{name}: evento id={chain[0].get('id')}",
            f"pertenece al expediente {chain[0].get('case_file_id')!r}, no a {case_file_id}",
        )
    if len(chain) != expected["count"]:
        missing = expected["count"] - len(chain)
        tail = f" (faltan {missing} eventos al final: cadena truncada)" if missing > 0 else ""
        check.fail(name, f"{len(chain)} eventos, el manifiesto declara {expected['count']}{tail}")
    if chain[-1].get("hash") != expected["last_hash"]:
        check.fail(name, "el hash del último evento no es events_chain.last_hash del manifiesto")
    if check.status == "ok":
        check.notes.append(
            f"{len(chain)} eventos encadenados, último hash {_short(expected['last_hash'])}"
        )


# --- Driver ------------------------------------------------------------------------------

TITLES = (
    "ZIP legible y seguro",
    "Manifiesto",
    "Firma del manifiesto",
    "Artefactos y hashes",
    "Instantánea de la revisión",
    "Cadena de eventos",
)


def verify_package(
    data: bytes, operator_key: Ed25519PublicKey | None = None, pinned_id: str | None = None
) -> list[Check]:
    """Run the six checks in order; a failed check 1 or 2 skips the rest."""
    checks = [Check(number, title) for number, title in enumerate(TITLES, start=1)]
    package = Package()
    steps: tuple[Callable[[Check], bool | None], ...] = (
        lambda check: check_zip(check, data, package),
        lambda check: check_manifest(check, package),
        lambda check: check_signature(check, package, operator_key, pinned_id),
        lambda check: check_artifacts(check, package),
        lambda check: check_snapshot(check, package),
        lambda check: check_events(check, package),
    )
    for index, (check, step) in enumerate(zip(checks, steps, strict=True)):
        try:
            proceed = step(check)
        except Exception as exc:  # hostile input must never crash the tool
            check.fail("verificador", f"error inesperado ({type(exc).__name__}: {exc})")
            proceed = False
        if proceed is False:
            for later in checks[index + 1 :]:
                later.status = "skipped"
                later.notes.append(f"no ejecutado: falló la comprobación {check.number}")
            break
    return checks


def summary(checks: Sequence[Check]) -> tuple[bool, str]:
    targets = [target for check in checks for target, _ in check.failures]
    if not targets:
        return True, "RESULTADO: OK. Paquete íntegro y firma válida (exit 0)."
    unique = ", ".join(dict.fromkeys(targets))
    failed = sum(1 for check in checks if check.status == "fail")
    return False, f"RESULTADO: FALLO en {failed} comprobación(es). Afectados: {unique} (exit 1)."


def render_text(checks: Sequence[Check], quiet: bool) -> str:
    lines: list[str] = []
    for check in checks:
        tag = {"ok": "[OK]  ", "fail": "[FAIL]", "skipped": "[--]  "}[check.status]
        if quiet and check.status != "fail":
            continue
        note = f": {'; '.join(check.notes)}" if check.notes and not quiet else ""
        lines.append(f"{tag} {check.number}. {check.title}{note}")
        lines.extend(f"       - {target}: {message}" for target, message in check.failures)
    lines.append(summary(checks)[1])
    return "\n".join(lines)


def render_json(
    checks: Sequence[Check], package_path: str, key_source: str, warnings: list[str]
) -> str:
    ok, text = summary(checks)
    document = {
        "verifier": f"sketch2cad-verify {VERSION}",
        "package": package_path,
        "public_key_source": key_source,
        "ok": ok,
        "exit_code": EXIT_OK if ok else EXIT_FAILED,
        "warnings": warnings,
        "checks": [
            {
                "number": check.number,
                "title": check.title,
                "status": check.status,
                "notes": check.notes,
                "failures": [{"target": t, "message": m} for t, m in check.failures],
            }
            for check in checks
        ],
        "summary": text,
    }
    return json.dumps(document, ensure_ascii=False, indent=2)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sketch2cad_verify.py",
        description=(
            "Verifica sin servidor un paquete final de Sketch2CAD: hashes de los artefactos, "
            "cadena de eventos y firma ed25519-jcs-v1 del manifiesto. Sale con 0 si todo "
            "coincide, 1 si algo falla (nombrando el artefacto o evento) y 2 ante errores de uso."
        ),
    )
    parser.add_argument("package", metavar="PAQUETE.zip", help="paquete ZIP a verificar")
    parser.add_argument(
        "--public-key",
        metavar="CLAVE.pem",
        help="clave pública Ed25519 de confianza (PEM o base64); por defecto la del paquete",
    )
    parser.add_argument(
        "--key-id", metavar="ed25519:...", help="identificador de clave esperado (fijación)"
    )
    parser.add_argument("--json", action="store_true", help="salida JSON legible por máquina")
    parser.add_argument(
        "--quiet", action="store_true", help="solo las comprobaciones fallidas y el resultado"
    )
    return parser.parse_args(argv)


def _read_limited(path: str, limit: int, what: str) -> bytes:
    target = Path(path)
    try:
        if not target.is_file():
            raise UsageError(f"{what} {path} no existe o no es un archivo")
        if target.stat().st_size > limit:
            raise UsageError(f"{what} {path} supera el límite de {limit} bytes")
        return target.read_bytes()
    except OSError as exc:
        raise UsageError(f"no se puede leer {what} {path} ({exc.strerror})") from None


def run(args: argparse.Namespace) -> int:
    if args.key_id is not None and not KEY_ID_PATTERN.match(args.key_id):
        raise UsageError("--key-id debe tener la forma ed25519:<16 dígitos hexadecimales>")
    operator_key: Ed25519PublicKey | None = None
    if args.public_key is not None:
        try:
            operator_key = load_public_key(
                _read_limited(args.public_key, MAX_KEY_FILE_BYTES, "la clave")
            )
        except (ValueError, UnsupportedAlgorithm):
            raise UsageError(f"{args.public_key} no es una clave pública Ed25519") from None
    data = _read_limited(args.package, MAX_TOTAL_BYTES, "el paquete")
    warnings = [] if operator_key is not None or args.key_id else [PROVENANCE_NOTICE]
    checks = verify_package(data, operator_key, args.key_id)
    ok = summary(checks)[0]
    if args.json:
        source = "argument" if operator_key is not None else "package"
        print(render_json(checks, args.package, source, warnings))
    else:
        if not args.quiet:
            print(f"sketch2cad-verify {VERSION}: paquete {args.package}")
        for warning in warnings:
            print(warning, file=sys.stderr if args.quiet else sys.stdout)
        print(render_text(checks, args.quiet))
    return EXIT_OK if ok else EXIT_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")
    args = parse_args(argv)
    try:
        return run(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
