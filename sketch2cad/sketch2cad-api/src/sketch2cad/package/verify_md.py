"""``VERIFY.md`` of the final package: how to verify it without the server (spec 14.2, US-17).

The text is Spanish (it is read by the people receiving the package) and depends only on
the arguments, so the same package gets the same bytes. It explains verification with
``sketch2cad-verify``, the manual verification with ``sha256sum`` and the public key, and
what the signature proves and does not prove.
"""

from collections.abc import Sequence

from sketch2cad.package.manifest import ArtifactEntry

VERIFY_FILENAME = "VERIFY.md"

_TEMPLATE = """# Verificación del paquete Sketch2CAD

Expediente `{case_code}`, plano `{plan_id}`, revisión {revision_number}.

Este paquete contiene:

| Archivo | Contenido |
|---|---|
| `{manifest}` | Manifiesto firmado (JSON canónico RFC 8785) con el SHA-256 de cada artefacto. |
{artifact_rows}| `{verify}` | Estas instrucciones (no forma parte del manifiesto). |
| `{public_key}` | Clave pública Ed25519 del servidor (PEM), identificador `{public_key_id}`. |

## Qué prueba la firma

La firma Ed25519 (esquema `ed25519-jcs-v1`) prueba integridad y origen técnico: el
manifiesto, y con él cada archivo cuyo SHA-256 lista, no cambió desde que la instalación de
Sketch2CAD dueña de la clave lo firmó. No prueba autoría pericial: no sustituye la firma
personal del perito, ni un sello de tiempo, ni la cadena de custodia física.

La clave pública incluida en el ZIP solo sirve si coincide con la que la institución publica
por un canal independiente. Compare el identificador `{public_key_id}` con el publicado: quien
altere el paquete puede también sustituir la clave y volver a firmar.

## Verificación con sketch2cad-verify

La herramienta `sketch2cad-verify` (carpeta `sketch2cad-api/verifier/` del repositorio;
requiere solo Python 3 con `cryptography` y `rfc8785`) recalcula el SHA-256 de cada artefacto,
reconstruye la cadena de eventos de `events.json` y verifica la firma del manifiesto:

```
python sketch2cad_verify.py paquete.zip
```

Sale con código 0 si todo coincide y con código 1 nombrando el artefacto o el evento que falla.

## Verificación manual

1. Extraiga el ZIP y calcule los hashes:

   ```
   sha256sum {hashed_files}
   ```

   Cada valor debe coincidir con el `sha256` de su `filename` en `artifacts` de
   `{manifest}`. El SHA-256 de `snapshot.json` es también `revision.snapshot_sha256`.

2. Verifique la firma. El mensaje firmado son los bytes JCS (RFC 8785) del manifiesto sin el
   campo `signature`; `signature.value` es la firma en base64:

   ```python
   import base64, json, rfc8785
   from cryptography.hazmat.primitives.serialization import load_pem_public_key

   manifest = json.load(open("{manifest}", "rb"))
   signature = manifest.pop("signature")
   key = load_pem_public_key(open("{public_key}", "rb").read())
   key.verify(base64.b64decode(signature["value"]), rfc8785.dumps(manifest))
   print("firma válida")
   ```

   `key.verify` lanza `InvalidSignature` si el manifiesto o la firma fueron alterados.

3. Verifique la cadena de eventos de `events.json` (eventos del expediente, del más antiguo al
   más reciente): el `prev_hash` del primero es el SHA-256 del texto del `case_file_id`; cada
   `hash` es `SHA-256(prev_hash || JCS(evento sin hash))` y es el `prev_hash` del siguiente.
   El número de eventos y el último `hash` deben coincidir con `events_chain` del manifiesto.
"""


def verify_markdown(
    *,
    case_code: str,
    plan_id: str,
    revision_number: int,
    manifest_filename: str,
    public_key_filename: str,
    public_key_id: str,
    artifacts: Sequence[ArtifactEntry],
) -> bytes:
    """UTF-8 bytes of ``VERIFY.md`` for a package holding ``artifacts`` (in that order)."""
    rows = "".join(f"| `{item.filename}` | Artefacto `{item.kind}`. |\n" for item in artifacts)
    return _TEMPLATE.format(
        case_code=case_code,
        plan_id=plan_id,
        revision_number=revision_number,
        manifest=manifest_filename,
        verify=VERIFY_FILENAME,
        public_key=public_key_filename,
        public_key_id=public_key_id,
        artifact_rows=rows,
        hashed_files=" ".join(item.filename for item in artifacts),
    ).encode("utf-8")
