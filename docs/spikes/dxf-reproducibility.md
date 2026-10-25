# Spike 2: reproducibilidad byte a byte del DXF con ezdxf

Decisión D-3 (DXF byte a byte o solo contenido canónico), criterio H0-11, requisitos REQ-12 y
REQ-16; prepara H4-4 (CA-08). Fecha de la medición: 2026-10-25.

Versiones medidas: **ezdxf 1.4.5** (MIT, fijada en `uv.lock`), **Python 3.12.15** (macOS,
arm64) y Python 3.12.13 (imagen `python:3.12.13-slim` del `Dockerfile`, Linux aarch64).

## Método

- Fixture `sketch2cad-api/spikes/fixtures/plan_fixture.json`: una revisión ficticia con
  `plan_id`, `revision_number`, `snapshot_sha256` y `frozen_at`, las 10 capas de §12.1 con su
  color ACI (incluidas `L_DIMENSIONS` y `L_SHEET`), una `LWPOLYLINE` cerrada de 4 vértices en
  `L_STRUCTURE`, un `TEXT` con estilo `S2C_TEXT` y texto no ASCII (`Habitación 1`), un `INSERT`
  del bloque `S2C_evidence_marker` con `ATTDEF`/`ATTRIB` `evidence_number` y una cota alineada
  generada con `render()` y texto sobreescrito por el valor declarado (`4,25 m`).
- Generador reutilizable `build_dxf(fixture) -> bytes` en `sketch2cad-api/spikes/dxf_repro.py`
  (§13.1): `R2013` (AC1027), `setup=True`, `doc.units = M`, `$INSUNITS = 6`,
  `$MEASUREMENT = 1`, metadatos en XDATA (`SKETCH2CAD`) de un `POINT` invisible en `L_SHEET` y
  `doc.audit()` sin errores ni correcciones (si no, `DxfValidationError`).
- `uv run python spikes/dxf_repro.py` lanza el generador en **dos procesos separados, con 2 s
  de diferencia** y con `PYTHONHASHSEED` distinto (1 y 4), primero sin ajustes (`raw`) y luego
  con ellos (`pinned`); imprime ambos SHA-256 y cada etiqueta DXF distinta (sección, variable de
  cabecera o entidad con su handle, código de grupo y los dos valores).
- Comprobaciones adicionales: `saveas()` frente a `write()` + `encode()`, finales de línea
  `\r\n`, segunda generación en el mismo proceso, `TZ=America/Santiago`,
  `EZDXF_DISABLE_C_EXT=1` (ruta Python pura) y ejecución en el contenedor Linux del proyecto.

## Campos que difieren sin ajustes

Salida real de la ejecución `raw` (10 etiquetas distintas):

| Campo | Ejemplo (proceso 1 / proceso 2) | Causa |
|---|---|---|
| `$TDCREATE` (código 40) | `2461339.1212847224` / `2461339.1213078704` | `ezdxf.new()` escribe `datetime.now()` en hora **local** |
| `$TDUPDATE` (código 40) | ídem | `Drawing._update_metadata()` lo reescribe con `datetime.now()` en cada `write()` |
| `$FINGERPRINTGUID` (código 2) | `{DEF9F97E-…}` / `{A88F3211-…}` | `uuid4()` al crear el documento |
| `$VERSIONGUID` (código 2) | `{2DEE96F8-…}` / `{254055FC-…}` | `uuid4()` en cada `write()` |
| `DICTIONARYVAR` `CREATED_BY_EZDXF` (handle 2E, código 1) | `1.4.5 @ 2026-10-25T05:54:39.395939+00:00` / `…41.549352…` | marcador de ezdxf con `datetime.now(UTC)` |
| `DICTIONARYVAR` `WRITTEN_BY_EZDXF` (handle B4, código 1) | ídem | ídem, en cada `write()` |
| Orden de `CLASS` `LAYOUT` y `ACDBPLACEHOLDER` (códigos 1 y 2, 4 etiquetas) | `ACDBPLACEHOLDER, LAYOUT` / `LAYOUT, ACDBPLACEHOLDER` | `ClassesSection.add_required_classes()` recorre el **set** `entitydb.dxf_types_in_use()`: el orden depende de `PYTHONHASHSEED` (con 12 semillas, 0 a 11, se invierte en 4, 7 y 10) |

Campos revisados que **no** difieren: handles y `$HANDSEED` (contador secuencial por documento,
`B5`), nombre del bloque anónimo de la cota (`*D1`) y su orden, `$TDUCREATE` y `$TDUUPDATE`
(ezdxf escribe un valor por defecto constante), `$TDINDWG` y `$TDUSRTIMER` (`0.0`), geometría
de la cota (ezdxf estima el ancho del texto por número de caracteres, sin leer fuentes del
sistema) y los valores en coma flotante (`repr`, independiente del locale y de las extensiones C).

## Ajustes aplicados (`pinned=True`, opción por defecto de `build_dxf`)

1. **Fechas de la revisión, no del reloj.** `$TDCREATE`, `$TDUCREATE`, `$TDUPDATE` y
   `$TDUUPDATE` = fecha juliana de `frozen_at` en UTC (no depende de `TZ`); `$TDINDWG` y
   `$TDUSRTIMER` = `0.0`.
2. **GUID derivados.** `$FINGERPRINTGUID = uuid5(NS, "plan:<plan_id>")` (identifica el plano en
   todas sus revisiones) y `$VERSIONGUID = uuid5(NS, "snapshot:<snapshot_sha256>")`, con un
   espacio de nombres fijo (`GUID_NAMESPACE`) y el formato `{XXXXXXXX-…}` de AutoCAD.
3. **Marcadores de ezdxf.** `CREATED_BY_EZDXF` y `WRITTEN_BY_EZDXF` =
   `"<versión de ezdxf> @ <frozen_at>"`.
4. **Orden de `CLASSES`.** Las clases requeridas conservan el orden de ezdxf y el resto se
   ordena por `(name, cpp_class_name)`.
5. **Escritura a flujo.** `doc.write(StringIO)` + `doc.encode()`: UTF-8 y `\n` en cualquier
   sistema operativo.

Los ajustes 1 a 4 no pueden hacerse antes de guardar porque `write()` llama a
`Drawing._update_metadata()` y reescribe `$TDUPDATE`, `$VERSIONGUID` y el marcador; por eso
`build_dxf` envuelve ese método **en la instancia** del documento (API privada de ezdxf
1.4.5). Se descartó la opción global `ezdxf.options.write_fixed_meta_data_for_testing`: es de
estado global del proceso, fija la fecha en 2000-01-01, usa un GUID nulo igual para todos los
archivos y no corrige el orden de `CLASSES`.

## Resultado

| Comprobación | Resultado |
|---|---|
| `raw`, 2 procesos, 2 s, semillas 1 y 4 | `51b4da7f…df661` ≠ `cf5c9611…10248`, 10 etiquetas distintas |
| `pinned`, 2 procesos, 2 s, semillas 1 y 4 | `238e2bcd44783ae668510f59bf26a2f69aa1504a47cde1ed60056fe9439314c2` en ambos, 0 diferencias |
| `pinned` con semillas 0 a 11 y `random` | mismo SHA-256 |
| `pinned` con `TZ=America/Santiago` | mismo SHA-256 |
| `pinned` con `EZDXF_DISABLE_C_EXT=1` | mismo SHA-256 |
| `pinned` en el contenedor (Linux aarch64, Python 3.12.13) | mismo SHA-256 |
| Segunda generación en el mismo proceso | mismos bytes |
| `saveas()` frente a `write()` + `encode()` (macOS) | mismos bytes |
| Variante `\r\n` | otro SHA-256 (`1938123…d2b75`) |
| `doc.audit()` tras releer con `ezdxf.readfile` | 0 errores, 0 correcciones |
| Relectura | 1 `LWPOLYLINE`, 1 `TEXT`, 1 `INSERT` con `ATTRIB evidence_number = 1`, 1 `DIMENSION` con texto `4,25 m`, XDATA y colores de capa correctos; coordenadas con tolerancia 1e-6 m |
| FreeCAD 1.1.4 (`freecadcmd`, `Import.readDXF`) | abre el archivo; respeta `$INSUNITS = 6` (polilínea de 0 a 4250 × 3125 mm); el bloque se importa como `App::Link`; texto y cota solo si se activan las preferencias de importación de textos y cotas del módulo Draft |

`sketch2cad-api/tests/test_spike_dxf_repro.py` (9 tests) repite estas comprobaciones en
subprocesos e incluye el control negativo: sin ajustes, `$TDCREATE`, `$TDUPDATE`,
`$FINGERPRINTGUID`, `$VERSIONGUID` y el orden de `CLASSES` difieren.

## Limitaciones

- El SHA-256 solo es estable para una versión fijada de ezdxf (el marcador incluye la versión y
  la salida de `setup=True` puede cambiar entre versiones) y de Python 3.12. Actualizar ezdxf exige
  regenerar los hashes de referencia y volver a pasar el test.
- El ajuste depende de un método privado (`Drawing._update_metadata`) y de
  `ClassesSection.classes`; si ezdxf los cambia, el test de doble generación falla.
- `saveas()` abre el archivo en modo texto: en Windows escribiría `\r\n` y cambiaría el hash. El
  exportador debe usar `build_dxf` (bytes) y guardar en binario. No se probó en Windows ni en
  Linux x86-64.
- El orden de las entidades sigue el del fixture: el exportador de H4 debe recorrer el snapshot
  en su orden canónico, porque los handles se asignan por orden de creación.
- En la imagen de ejecución ezdxf avisa de que no puede crear `/app/.cache/ezdxf` (el usuario
  `app` no puede escribir en su `HOME`); no afecta a los bytes, pero H4 debe fijar
  `XDG_CACHE_HOME` o aceptar el aviso.
- No se probaron LibreCAD ni QCAD (no instalados).

Recomendación: byte-identical
