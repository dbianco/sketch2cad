# Higiene de logs: escaneo de los logs de prueba (H6-8, REQ-11)

REQ-11 exige que los logs de la aplicación lleven solo identificadores: ni imágenes, ni geometría, ni coordenadas, ni nombres de archivo de croquis. Dos defensas lo cubren:

1. **En el código** (H1-9): `sketch2cad.logging` reemplaza bytes por su longitud y omite el mensaje de las excepciones; `tests/test_log_hygiene.py` sube un croquis con EXIF y GPS y comprueba que ningún registro los repite, y prohíbe llamadas `logger.*` que mencionen `filename`, `geometry` o `coords`.
2. **Sobre los logs reales de CI** (H6-8): `sketch2cad-api/scripts/scan_logs.py` recorre los logs de la ejecución completa de las pruebas y falla si encuentra coordenadas o nombres de croquis.

## Cómo funciona

```
cd sketch2cad-api
uv run python scripts/scan_logs.py [--patterns ARCHIVO] [--names-file ARCHIVO ...] LOG [LOG ...]
```

`LOG` puede ser `-` para leer la entrada estándar. Código de salida: 0 limpio, 1 hay hallazgos, 2 error de uso, de configuración o de lectura.

Reglas (identificador entre paréntesis):

- Coordenadas: tuplas `(x, y)` (`coord-tuple`), listas `[x, y]` (`coord-list`; no cuenta si va pegada a una palabra, como los identificadores de parámetros de pytest `test_x[-4,20]`), asignaciones `x=`, `lat:`, `gps…=` (`coord-assignment`), claves de geometría seguidas de un valor (`"coords_m": [[`, `"geometry": {`, cualquier clave `*_m`, `*_mm`, `*_px` o `*_points` con una lista u objeto; `geometry-json`), WKT (`POINT (`, `LINESTRING (`; `wkt`) y grados-minutos GPS (`gps-dms`).
- Nombres de archivo: cualquier `*.jpg|jpeg|png|tif|tiff|pdf|heic` cuyo nombre base no esté en la lista permitida (`sketch-filename`), y cualquier nombre de la sección `[names]` o de un `--names-file` (`validation-name`).

La lista permitida y los patrones de nombres viven en un único archivo, `sketch2cad-api/scan_logs_patterns.toml`: los fixtures sintéticos que el repositorio versiona (`synthetic_sketch.png`, `dimensioned_sketch.png`, `one_page.pdf`…), los nombres que el producto escribe en los paquetes (`plan.pdf`, `audit.pdf`, `original.*`) y los nombres genéricos que usan las pruebas (`croquis.png`, `foto.jpg`…). Una prueba falla si se versiona un fixture de imagen o PDF que no figure en la lista. `[names] globs` recoge los esquemas de nombres del conjunto de validación (`validation/sketches/*`, `croquis-validacion-*`).

Cada hallazgo se imprime como `LOG:LÍNEA: REGLA: FRAGMENTO`. El texto encontrado se sustituye por `[REDACTED]` y todos los demás dígitos de la línea por `#`, y el fragmento se limita a una ventana de 120 caracteres que empieza poco antes del primer hallazgo: la salida nunca repite una coordenada ni un nombre de croquis, así que puede quedar en el log de CI sin convertirse en la fuga que busca.

## En CI

El job `test` de `.github/workflows/api-ci.yml` ejecuta `uv run pytest -q --log-file=ci-app.log --log-file-level=DEBUG` copiando la salida de consola a `ci.log` (con `tee`); los logs de la aplicación de todos los loggers, a nivel DEBUG, quedan en `ci-app.log`. El paso siguiente escanea ambos, también cuando las pruebas fallan (la salida de un fallo es el lugar más probable de una fuga). Ninguno de los dos archivos se sube como artefacto.

## Nombres reales del conjunto de validación

Los croquis del conjunto de validación (tarea 001-115) son dibujos reales a mano: sus nombres no se versionan nunca, ni en `scan_logs_patterns.toml` (una prueba exige que `[names] literal` esté vacío). Para incluirlos en el escaneo:

- **En CI**: crear el secreto de repositorio `VALIDATION_NAMES` con un nombre por línea (las líneas vacías y las que empiezan por `#` se ignoran). El paso lo escribe en un archivo temporal con permisos `0600`, lo pasa con `--names-file` y lo borra al terminar; nunca lo imprime. Sin el secreto (por ejemplo, en pull requests desde forks) el escaneo corre solo con los patrones versionados.
- **En local**: `uv run python scripts/scan_logs.py --names-file ~/ruta/fuera/del/repo/nombres.txt ci.log`.

Cada entrada se busca como subcadena sin distinguir mayúsculas. Conviene listar el nombre completo y también la raíz sin extensión (`croquis-cruce-07.jpg` y `croquis-cruce-07`), porque el escaneo no deriva una de la otra.

## Medición sobre una ejecución real (2026-10-26, macOS arm64)

- `uv run pytest -q` (1565 pruebas): 0 hallazgos.
- La misma ejecución con `-o log_cli=true --log-cli-level=DEBUG` (17 076 líneas; unas 7 600 de loggers `sketch2cad.*`, más httpx, ezdxf, alembic, procrastinate y psycopg): 4 falsos positivos de `coord-list` con la primera versión de las reglas, todos identificadores de parámetros de pytest con decimales con coma (`test_parse_distance_rejects_with_the_v04_message[-3,5]`); se corrigió la regla (no cuenta una lista pegada a una palabra) y el resultado quedó en 0 hallazgos.
- Revisión del código: el aviso de exportación fallida registraba `package build failed: {mensaje de la excepción}`, y algunos mensajes de exportación citan valores de geometría (`SnapshotError` incluye el valor no finito) o errores de Pillow. Ahora registra solo el tipo de la excepción, como ya hacía el borrador.

## Límites

- Es una búsqueda con expresiones regulares, no una prueba: un resultado limpio es evidencia, no demostración. No detecta coordenadas sueltas (`4.2137` sin contexto), nombres con espacios completos (marca solo la parte final `5.jpg`), nombres codificados (URL, base64, hex) ni nombres de archivo sin extensión que no estén en `--names-file`.
- Solo escanea lo que recibe: `ci.log` y `ci-app.log` del job `test`. No cubre los logs de los contenedores de `compose-smoke`, de las E2E ni los de producción.
- Una tupla de dos números en un log (`(1, 2)` en un mensaje de fallo de pytest) cuenta como hallazgo aunque no sea una coordenada; si aparece un falso positivo, se ajusta la regla en el script con su prueba, nunca ignorando archivos enteros.
- El paso de CI no se ha ejecutado todavía en GitHub Actions; se validó localmente simulando sus comandos.
