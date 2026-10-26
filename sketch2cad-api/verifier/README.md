# sketch2cad-verify

Verificador independiente de los paquetes finales de Sketch2CAD (ZIP con `manifest.json`,
`plan.dxf`, `plan.pdf`, `audit.pdf`, `original.<ext>`, `events.json`, `snapshot.json`,
`VERIFY.md` y `public_key.pem`). Comprueba, sin acceso al servidor ni a la base de datos, que
ningún archivo del paquete cambió desde que la instalación de Sketch2CAD lo firmó y que la
cadena de eventos del expediente está completa e íntegra.

Es un único archivo, `sketch2cad_verify.py`, que no importa nada de la aplicación: puede
copiarse solo y entregarse a un auditor externo.

## Instalación

Requiere Python 3.11 o posterior y dos paquetes:

```
pip install cryptography rfc8785
```

## Uso

```
python sketch2cad_verify.py paquete.zip [--public-key CLAVE.pem] [--key-id ed25519:...] [--json] [--quiet]
```

| Opción | Efecto |
|---|---|
| `--public-key CLAVE.pem` | Clave pública Ed25519 de confianza (PEM o base64 de los 32 bytes). Si se omite, se usa `public_key.pem` del paquete. |
| `--key-id ed25519:...` | Identificador esperado de la clave; falla si la clave usada tiene otro. |
| `--json` | Informe JSON legible por máquina (`ok`, `exit_code`, `warnings`, `checks`, `summary`). |
| `--quiet` | Solo las comprobaciones fallidas y la línea de resultado. |

El paquete se lee en memoria: no se usa la red ni se escriben archivos temporales.

## Comprobaciones

Se ejecutan en este orden y cada una imprime una línea `[OK]` o `[FAIL]` (`[--]` si no se
ejecutó porque falló una anterior imprescindible):

1. **ZIP legible y seguro.** Nombres de miembro simples y únicos (sin rutas, `..`, rutas
   absolutas ni unidades), sin cifrado, como máximo 64 miembros y 500 MB sin comprimir en
   total (protección contra bombas ZIP; el archivo ZIP también se limita a 500 MB), y
   `manifest.json` presente.
2. **Manifiesto.** JSON estricto (sin claves duplicadas ni `NaN`), bytes idénticos a su forma
   canónica RFC 8785 (JCS), `manifest_version` 2.0 y los campos que usan las siguientes
   comprobaciones.
3. **Firma.** Esquema `ed25519-jcs-v1`, `signature.public_key_id` igual al identificador de la
   clave y firma Ed25519 válida sobre los bytes JCS del manifiesto sin el campo `signature`.
4. **Artefactos.** Cada archivo de `artifacts` existe en el ZIP con el SHA-256 registrado; no
   hay otros miembros además de `manifest.json`, `VERIFY.md` y `public_key.pem`.
5. **Instantánea.** El SHA-256 de `snapshot.json` es `revision.snapshot_sha256` y su `plan_id`
   y `revision_number` coinciden con el manifiesto.
6. **Cadena de eventos.** `events.json` se reconstruye evento a evento:
   `hash = SHA-256(prev_hash || JCS(evento sin hash))`, con el primer `prev_hash` igual al
   SHA-256 del `case_file_id`. Se informa el id y el índice del primer evento inválido y el
   motivo. El número de eventos y el último hash deben ser `events_chain.count` y
   `events_chain.last_hash` (así se detecta una cadena truncada al final), y todos los
   eventos deben pertenecer a `case_file.id`.

`VERIFY.md` no está cubierto por el manifiesto: es solo informativo.

## Ejemplo de salida

```
$ python sketch2cad_verify.py paquete.zip
sketch2cad-verify 1.0: paquete paquete.zip
AVISO: se usa la clave pública incluida en el paquete (public_key.pem). Eso solo prueba la consistencia interna: [...]
[OK]   1. ZIP legible y seguro: 9 miembros, 200870 bytes sin comprimir
[OK]   2. Manifiesto: JSON canónico RFC 8785, manifest_version 2.0
[OK]   3. Firma del manifiesto: ed25519-jcs-v1 válida, clave ed25519:733de1a858cd3014 (public_key.pem del paquete)
[FAIL] 4. Artefactos y hashes: VERIFY.md no está cubierto por el manifiesto (informativo)
       - plan.dxf: el SHA-256 no coincide (manifiesto 76011d92bdb9c7ff…, calculado 7ddee9e88e100587…)
       - events.json: el SHA-256 no coincide (manifiesto 25c547518606b771…, calculado 978d011b36ef51d5…)
[OK]   5. Instantánea de la revisión: SHA-256 = revision.snapshot_sha256 (5f6f2e38b92a635c…)
[FAIL] 6. Cadena de eventos
       - events.json: evento id=19: índice 3: el hash no coincide con el contenido (código hash_mismatch)
RESULTADO: FALLO en 2 comprobación(es). Afectados: plan.dxf, events.json, events.json: evento id=19 (exit 1).
```

## Códigos de salida

| Código | Significado |
|---|---|
| 0 | Todas las comprobaciones pasaron. |
| 1 | Alguna comprobación falló; la salida nombra el artefacto o el evento afectado. |
| 2 | Error de uso: argumentos inválidos, paquete o clave inexistente o ilegible, `--key-id` mal formado. |

## Qué prueba y qué no prueba

Una verificación correcta prueba **integridad y origen técnico**: los archivos son los que la
instalación de Sketch2CAD dueña de la clave firmó, sin un solo byte cambiado, y la cadena de
eventos del expediente no fue alterada, reordenada ni truncada.

**No prueba autoría pericial**: no sustituye la firma personal del perito, ni un sello de
tiempo, ni la cadena de custodia física. Tampoco dice nada sobre la exactitud del contenido
del plano.

## Fijar la clave

La clave `public_key.pem` del paquete solo prueba consistencia interna: quien altere el
paquete puede sustituir la clave y volver a firmar. Por eso, si no se indica clave, el
verificador imprime un aviso. Para probar el origen use la clave o el identificador que la
institución publica por un canal independiente:

```
python sketch2cad_verify.py paquete.zip --public-key clave_institucion.pem
python sketch2cad_verify.py paquete.zip --key-id ed25519:733de1a858cd3014
```

Con `--public-key`, además, `public_key.pem` del paquete debe ser la misma clave.

## Relación con el esquema de firma

El esquema `ed25519-jcs-v1` es el de `sketch2cad/package/signing.py`: el campo `signature` del
manifiesto tiene `scheme`, `public_key_id` (`ed25519:` más los primeros 16 dígitos
hexadecimales del SHA-256 de los 32 bytes de la clave pública) y `value` (firma Ed25519 en
base64 estándar). El mensaje firmado son los bytes RFC 8785 del manifiesto sin `signature`;
`manifest.json` guarda los bytes RFC 8785 del manifiesto firmado.

El verificador contiene copias mínimas de `verify_chain` (`sketch2cad/audit/verify.py`) y de
`verify_manifest` (`sketch2cad/package/signing.py`). `tests/package/test_verifier.py` ejecuta
ambas implementaciones sobre las mismas entradas para detectar divergencias: si cambia el
módulo de la aplicación, actualice también la copia.
