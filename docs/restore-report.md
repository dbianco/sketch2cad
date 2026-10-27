# Informe de la prueba de respaldo y restauración (tarea 001-119)

Ejecución de `scripts/backup.sh` y `scripts/restore_check.sh` sobre una instancia poblada, criterio H6-7 del Jot Down (REQ-10, REQ-11): «La restauración desde `pg_dump` más el directorio de objetos reproduce los mismos `sha256` de los originales y de los snapshots validados». Criterio de la tarea: `bash scripts/restore_check.sh <backup>` sale con 0 y el informe lista el número de originales y de snapshots cuyo SHA-256 coincide. Procedimiento de referencia: [docs/backup.md](backup.md).

## Resumen

| Ejecución | Fecha (UTC) | Árbol probado | Resultado |
|---|---|---|---|
| 1 | 2026-10-27 01:52–02:02 | `eb60ea5`, árbol limpio | **FAIL**: `restore_check.sh` no verificaba los snapshots congelados ni informaba de ellos; un snapshot alterado pasaba con `PASS` y código 0 |
| — | — | Corrección en el árbol de trabajo (sin commit): `restore_check.sh`, `docs/backup.md`, `tests/test_backup_scripts.py` | — |
| 2 | 2026-10-27 02:11–02:16 | `eb60ea5` **más los cambios sin commit** | **PASS**: sale con 0 e imprime `Originals matched: 3 of 3` y `Snapshots matched: 3 of 3`; los tres controles negativos salen con 1 y nombran el objeto, la fila `source` o la revisión |

**Resultado final: PASS** (ejecución 2), con dos salvedades que hay que tener en cuenta al cerrar la tarea:

- Se probó la **punta de la rama** `ai/001-119-backup-restore-check` (`eb60ea5`), que **no** está fusionada en `main`, y con cambios **sin commit**. El resultado vale para los ficheros con los SHA-256 de la tabla «Árbol probado» de la ejecución 2. Si el commit de la corrección cambia `restore_check.sh`, esta prueba deja de describirlo.
- Las imágenes de la pila no incluyen `scripts/` ni `tests/` (`.dockerignore`), así que los cambios sin commit no afectan a lo que corre en la pila: solo a `restore_check.sh`, que corre en el host.

## Ejecución 1: FAIL (resumen)

Ejecución completa del 2026-10-27, 01:52–02:02 UTC, sobre `eb60ea5` con el árbol limpio. Proyecto de compose `sdlc-acc-001-119-r1`, `WEB_PORT=18119`, `WORKER_WITH_OCR=false`, imágenes `sdlc-acc-001-119-r1-*:eb60ea5` y base temporal de restauración `sdlc-acc-001-119-r1-restore-pg` en `127.0.0.1:15119`. La instancia se pobló por la API con el mismo guion que la ejecución 2 (mismas cifras: 2 expedientes, 3 originales, 3 revisiones congeladas, 9 artefactos, 11 objetos, 89 eventos en 3 cadenas).

- `backup.sh`: código 0, 0,28 s, 11 objetos.
- `restore_check.sh` sobre el respaldo íntegro: código 0, 1,56 s. Imprimía `Objects checked: 11 valid`, `Sources checked: 3`, `Event chains checked: 3` y `PASS`, pero **ninguna línea sobre snapshots**. El script no leía la tabla `revision`, y su comentario («Frozen snapshots (revisions) arrive in H2; they are objects too, so check 2 covers them») era falso: los snapshots viven en la base (`revision.snapshot`, JSONB, con `revision.snapshot_sha256`), no en el almacenamiento de objetos. Además, `Sources checked: N` contaba filas revisadas, no filas que coinciden.
- Una comprobación independiente (fuera del script) recalculó `snapshot_sha256(snapshot)` en una restauración aparte: 3 de 3 coincidían. Los datos del respaldo estaban íntegros; lo que fallaba era la comprobación.
- Controles negativos: con un byte invertido en un original, con `SHA256SUMS` sin actualizar y regenerado, el script salió con **1** y nombró el objeto y la fila `source`. Con un snapshot alterado (primera coordenada de una línea de `2.0` a `2.01`, volcado otra vez y `SHA256SUMS` regenerado), la comprobación independiente lo detectaba (`Frozen snapshots: 3, SHA-256 matched: 2`), pero `restore_check.sh` salió con **0** y `PASS`.
- Limpieza completa y los 12 contenedores previos sin cambios.

Pedía: recalcular el hash de cada revisión congelada y compararlo con `revision.snapshot_sha256` y con el evento de congelación; imprimir `FAIL revision <id>: ...`; informar de coincidencias (`Originals matched: M of N`, `Snapshots matched: M of N`); corregir el comentario del script y docs/backup.md; repetir la ejecución desde cero.

## Corrección

Cambios en el árbol de trabajo, sin commit (`git diff --stat` contra `eb60ea5`):

```text
 docs/backup.md                              |   7 +-
 sketch2cad-api/scripts/restore_check.sh     |  63 ++++++++++++-
 sketch2cad-api/tests/test_backup_scripts.py | 135 ++++++++++++++++++++++++++--
 3 files changed, 191 insertions(+), 14 deletions(-)
```

- `restore_check.sh`: nueva comprobación 4. Para cada `revision` con `state = 'frozen'` recalcula `sketch2cad.revisions.snapshot.snapshot_sha256(snapshot)` y lo compara con `revision.snapshot_sha256` y con el `snapshot_sha256` de su evento `revision.frozen`. Imprime `FAIL revision <id>: ...` por cada discrepancia y las líneas `Originals matched: M of N` y `Snapshots matched: M of N`. Se quitó el comentario falso.
- `docs/backup.md`: describe la comprobación de snapshots y las nuevas líneas del informe, y corrige el punto de «Limitaciones».
- `tests/test_backup_scripts.py`: el fixture congela una revisión; dos pruebas nuevas cambian una coordenada del snapshot y `snapshot_sha256`.

## Ejecución 2: PASS

### Datos de la ejecución

| Campo | Valor |
|---|---|
| Fecha | 2026-10-26, 23:11–23:16 hora local (UTC−3); 2026-10-27T02:11:54Z–02:15:55Z |
| Rama y commit | `ai/001-119-backup-restore-check`, `eb60ea5a6f1766974e3c45feba44e49c282abf95` (punta de la rama, apilada sobre 001-114; **no** está en `main`) |
| Árbol de trabajo | `eb60ea5` **más cambios sin commit** en los tres ficheros de la corrección. Además, `docs/restore-report.md` sin seguimiento (este informe) y `sketch1.jpg` / `sketch2.jpeg` sin seguimiento en la raíz (ficheros del usuario, sin usar) |
| Host | macOS 26.5.2 (25F84), arm64 (Apple M4 Pro); 44 GiB libres en disco |
| Docker | Docker Desktop, Engine 29.6.1, Compose v5.3.0 |
| uv | 0.12.5 (Python del proyecto, 3.12) |
| PostgreSQL | `postgres:16`, 16.14 (Debian 16.14-1.pgdg13+1) en la pila y en la base temporal |

### Árbol probado

| Fichero | SHA-256 (antes y después de la ejecución, sin cambios) |
|---|---|
| `sketch2cad-api/scripts/restore_check.sh` | `7343251826dc76d6067555e565fab2e4cad430c0e0b9d1fbd9bcd33531a40bfc` |
| `docs/backup.md` | `e9f9649df7b3ea5d7ef800211156465e35cb2f16c2913ef7affe1954ba49f7af` |
| `sketch2cad-api/tests/test_backup_scripts.py` | `02680a54f3056c7f90332d81cd683c61a4c5a1bde308594a3dbef8d4b2ece0ba` |

Imágenes construidas desde `eb60ea5` (sin cambios en `src/`, `migrations/`, Dockerfiles, compose ni `sketch2cad-web/`): `sdlc-acc-001-119-r2-api:eb60ea5` (`138ba9d44161`), `-worker:eb60ea5` (`b2c8aae03cb7`), `-web:eb60ea5` (`27b28ff6d066`). La construcción tardó 10 s porque todas las capas salieron de la caché de BuildKit, que va indexada por el contenido de los ficheros copiados.

### Aislamiento

| Parámetro | Valor |
|---|---|
| Proyecto de compose | `sdlc-acc-001-119-r2` (volúmenes `sdlc-acc-001-119-r2_db-data`, `_objects`, `_signing-keys`) |
| Puertos | `WEB_PORT=18120` (solo `web`, en `127.0.0.1`); `db` y `api` sin publicar |
| Worker | `WORKER_WITH_OCR=false` |
| Imágenes | etiquetas propias mediante un override fuera del repositorio (solo cambia la clave `image`); no se reetiquetaron las `sketch2cad-*:local` compartidas |
| Secretos | `POSTGRES_PASSWORD`, `SESSION_SECRET` y contraseñas de los usuarios aleatorios (`openssl rand`), solo en memoria. La clave de firma se generó en el volumen (`public_key_id=ed25519:3041f4f7514937dc`, dato público). Nada se escribió en disco ni aparece aquí |
| Base temporal de restauración | `sdlc-acc-001-119-r2-restore-pg` (`postgres:16`, `--rm`), `127.0.0.1:15120`, contraseña aleatoria en memoria |

No se tocó ningún `textralocal-*`, los puertos 5432/5433 ni otros proyectos. `sketch2cad-pg-dev` (puerto 5434) solo se usó como base de las pruebas unitarias, que es su función según el contexto de los agentes: las pruebas trabajan en esquemas aislados que borran al terminar. El contenedor no se paró, reinició ni modificó.

### Comandos

Desde `sketch2cad-api/`, con `C=(docker compose -p sdlc-acc-001-119-r2 -f docker-compose.yml -f <override>)` y `SIGNING_KEY_PATH=/app/keys/signing-key.pem APP_ENV=dev WEB_PORT=18120 WORKER_WITH_OCR=false`:

```sh
"${C[@]}" build                                                          # 10 s (caché)
"${C[@]}" run --rm --no-deps api python -m sketch2cad.cli generate-signing-key --if-missing
"${C[@]}" up -d --wait --wait-timeout 300                                # 13 s, 4 servicios healthy
printf '%s\n' "$PASS" | "${C[@]}" exec -T api python -m sketch2cad.cli create-user --username <u> --role technician|reviewer
python3 -I populate.py http://127.0.0.1:18120 <resumen> ../sketch2cad-web/e2e/fixtures    # 10 s
export DATABASE_URL="postgresql://sketch2cad:<contraseña>@db:5432/sketch2cad" PG_CONTAINER="$("${C[@]}" ps -q db)"
docker cp "$("${C[@]}" ps -q api)":/app/storage/objects "$SCRATCH/live_storage/"
export STORAGE_ROOT="$SCRATCH/live_storage"
bash scripts/backup.sh "$SCRATCH/backups"

docker run -d --rm --name sdlc-acc-001-119-r2-restore-pg -e POSTGRES_USER=sketch2cad \
  -e POSTGRES_PASSWORD=<aleatoria> -p 127.0.0.1:15120:5432 postgres:16
export DATABASE_URL="postgresql://sketch2cad:<aleatoria>@localhost:15120/postgres"
export STORAGE_ROOT="$SCRATCH/live_storage"
bash scripts/restore_check.sh "$SCRATCH/backups/20261027T021353Z"
```

`$SCRATCH` es un directorio temporal de la sesión, fuera del repositorio. `populate.py` es el guion de la ejecución 1, reutilizado sin cambios. Las contraseñas se leyeron con `docker inspect` hacia una variable, sin imprimirlas.

Adaptaciones, las mismas que en la ejecución 1: copia de `objects/` al host con `docker cp`, porque en Docker Desktop la ruta `Mountpoint` del volumen no existe en el host; contraseña de la base leída del contenedor; override de etiquetas. No se modificó ningún script ni código durante la ejecución.

**Incidencia registrada.** En el primer intento (02:12:44Z) los comandos se lanzaron desde zsh con `$C` como cadena. zsh no la parte en palabras, así que todos los `docker compose` fallaron con código 127 sin crear nada (comprobado: ni contenedores ni volúmenes `r2`), y `populate.py` falló con `Connection refused`. Se repitió desde un guion bash con `C` como array, sin ningún otro cambio. También falló una consulta mía de recuento (`ORDER BY created_at`: `source` no tiene esa columna); se repitió ordenando por `id`. Ninguno de los dos fallos afecta a los scripts probados.

### Instancia poblada

El mismo recorrido que en la ejecución 1, por la API real (proxy `/api/` de `web` en el 18120), con un técnico y un revisor creados con el CLI:

- Expediente 1: sube `synthetic_sketch.png` y `dimensioned_sketch.png` (QC `succeeded`). «Plano del cruce»: calibración, 26 sugerencias (3 aceptadas y 23 rechazadas), 5 entidades manuales, `VALIDATED` (**se congela la revisión 1**), exportación final `succeeded`, reapertura, indicio 2, exportación borrador `succeeded` y otra vez `VALIDATED` (**se congela la revisión 2**). «Plano acotado»: calibración, 4 sugerencias rechazadas, muro y cota de 3,50 m, `VALIDATED` (**se congela la revisión 1**).
- Expediente 2: vuelve a subir `dimensioned_sketch.png`, una segunda fila `source` sobre el mismo objeto.

Recuento en la base viva antes del respaldo:

| Elemento | Número |
|---|---|
| Expedientes | 2 |
| **Originales** (filas `source`) | **3** (2 SHA-256 distintos, PNG, sin `raster_key`) |
| Planos | 2 |
| Revisiones | 3, **las 3 congeladas** (snapshots), con 3 eventos `revision.frozen` |
| Entidades | 46 |
| Artefactos | 9: final (`dxf`, `pdf_plan`, `pdf_audit`, `manifest`, `zip`), borrador (`dxf`, `pdf_plan`) y 2 `rectified_image` |
| Ficheros en `objects/` | 11 (2 originales y 9 artefactos), 1,1 MB |
| Eventos / cadenas | 89 / 3 |

Originales:

| Fila `source` | Expediente | SHA-256 |
|---|---|---|
| `5d7c0e6a-ca1b-4d7d-85d5-10a5d70be393` (`synthetic_sketch.png`) | 1 | `0472a6937a52c58a60ad19dbcd1f5d91863921c3f3f781c0ca7ea34bc1a7be71` |
| `d99a1e8f-80fd-4c35-9420-f531d54d0940` (`dimensioned_sketch.png`) | 1 | `6e77b89c3b4781475a3edd78a52fd967c3af665226a6a366770dc61e76136f12` |
| `ce085d40-7aeb-45a7-8fec-f5fc09610d14` (`dimensioned_sketch.png`) | 2 | `6e77b89c3b4781475a3edd78a52fd967c3af665226a6a366770dc61e76136f12` |

Snapshots congelados (`revision.snapshot_sha256`; el evento `revision.frozen` de cada una registra el mismo valor):

| Revisión | Plano | n.º | `snapshot_sha256` |
|---|---|---|---|
| `0ca792c9-104e-45f6-ac29-add19851daec` | Plano del cruce | 1 | `14c7f392ee2cde7f52d5ad66f62de9e3bb3c1015ff9f4893544097625e5917d4` |
| `ebe72ccb-562d-4dc7-8df4-33ed8c25621f` | Plano del cruce | 2 | `3aafb18622530c4d6a4a44c5f73afbcd393a93d0ddb0259e18fc3877eccda244` |
| `0d8f64be-0744-4d96-8ff7-73aec2349d5c` | Plano acotado | 1 | `67cafedcc4e0887132ec60538f824684a87e829afaaad0b4ef52ac5d616b4753` |

Los identificadores y hashes de snapshot son distintos de los de la ejecución 1 porque es una instancia nueva (los snapshots incluyen `plan_id`). Los SHA-256 de los originales coinciden con los de la ejecución 1, porque son los mismos ficheros.

### Salida de `backup.sh`

Código de salida **0**, **0,25 s**.

```text
Step 1/3: pg_dump (docker:7048456af33c577222e7ca50a233971eb81cbdc69892de108835ee436138ac51) of the whole database
Step 2/3: tar of $SCRATCH/live_storage/objects
Step 3/3: MANIFEST and SHA256SUMS
Backup written to $SCRATCH/backups/20261027T021353Z (11 objects)
BACKUP_DIR=$SCRATCH/backups/20261027T021353Z
```

Contenido: `pg.dump` (107 986 bytes), `objects.tar` (1 189 376 bytes, 11 ficheros), `MANIFEST` y `SHA256SUMS`:

```text
created_at=2026-10-27T02:13:53Z
schema=public
objects=11
pg_mode=docker:7048456af33c577222e7ca50a233971eb81cbdc69892de108835ee436138ac51
pg_dump_version=pg_dump (PostgreSQL) 16.14 (Debian 16.14-1.pgdg13+1)

a756128c419a0719eda28851eae2a6106e751236803613d907fa3d145146ee6c  pg.dump
535c8b00414730deb25f85850d8f76849358a2bcea06a3b7ccfb6e8c3f15384c  objects.tar
```

### Salida de `restore_check.sh` (criterio H6-7)

Código de salida **0**, **1,67 s**. `pg_restore` corrió dentro de `sdlc-acc-001-119-r2-restore-pg`, el único contenedor que publica el 15120.

```text
Using scratch directory $TMPDIR/restore_check.HOoCnr
Step 1/3: extract objects.tar
Step 2/3: pg_restore (docker:sdlc-acc-001-119-r2-restore-pg) into scratch database restore_check_1793067296_94122_27402
Using scratch database restore_check_1793067296_94122_27402
Step 3/3: verify objects, sources, snapshots and event chains
Objects checked: 11 valid
Objects compared with $SCRATCH/live_storage: 11
Sources checked: 3
Originals matched: 3 of 3
Snapshots matched: 3 of 3
Event chains checked: 3
PASS: backup $SCRATCH/backups/20261027T021353Z restored and verified
```

| Elemento | Informe del script | Total en la base viva |
|---|---|---|
| Originales | `Originals matched: 3 of 3` | 3 filas `source` |
| Snapshots | `Snapshots matched: 3 of 3` | 3 revisiones congeladas |

Las dos líneas que pide el criterio aparecen y son iguales al total. Después de esta y de cada una de las ejecuciones siguientes, no quedó ninguna base `restore_check_*` en el servidor temporal ni ningún directorio `restore_check.*` en `$TMPDIR`.

### Controles negativos

Todos sobre **copias** en `$SCRATCH/tampered/`, nunca sobre el respaldo original ni sobre los volúmenes vivos. Al terminar, `shasum -c SHA256SUMS` del respaldo original daba `pg.dump: OK objects.tar: OK` y el objeto vivo `0472a693…` conservaba su hash.

**1. Byte invertido en un original.** Objeto de `synthetic_sketch.png` (`sha256/04/72/0472a693…`, fila `source` `5d7c0e6a-…`): byte del desplazamiento 18 440 de 36 881, `0x6c` → `0x93`, en la copia extraída (con `chmod u+w`, porque los objetos se extraen con modo 0444). Se volvió a crear `objects.tar` en dos variantes.

a) `SHA256SUMS` sin actualizar: código **1**, 1,11 s.

```text
Objects checked: 10 valid
Objects compared with $SCRATCH/live_storage: 10
Sources checked: 3
Originals matched: 2 of 3
Snapshots matched: 3 of 3
Event chains checked: 3
FAIL archive objects.tar: SHA-256 differs from SHA256SUMS
FAIL object sha256/04/72/0472a6937a52c58a60ad19dbcd1f5d91863921c3f3f781c0ca7ea34bc1a7be71: content SHA-256 is b572f0ca74718a6e7ee4649df2b3237c7c3600fb26f7723aa07433750391b198
FAIL source 5d7c0e6a-ca1b-4d7d-85d5-10a5d70be393: object sha256/04/72/0472a6937a52c58a60ad19dbcd1f5d91863921c3f3f781c0ca7ea34bc1a7be71 missing or invalid
FAIL: restore check of $SCRATCH/tampered/stale-sums found problems
```

b) `SHA256SUMS` regenerado: código **1**, 1,01 s. Nombra el objeto y la fila `source`.

```text
Objects checked: 10 valid
Objects compared with $SCRATCH/live_storage: 10
Sources checked: 3
Originals matched: 2 of 3
Snapshots matched: 3 of 3
Event chains checked: 3
FAIL object sha256/04/72/0472a6937a52c58a60ad19dbcd1f5d91863921c3f3f781c0ca7ea34bc1a7be71: content SHA-256 is b572f0ca74718a6e7ee4649df2b3237c7c3600fb26f7723aa07433750391b198
FAIL source 5d7c0e6a-ca1b-4d7d-85d5-10a5d70be393: object sha256/04/72/0472a6937a52c58a60ad19dbcd1f5d91863921c3f3f781c0ca7ea34bc1a7be71 missing or invalid
FAIL: restore check of $SCRATCH/tampered/fixed-sums found problems
```

**2. Coordenada de un snapshot congelado cambiada** (el control que en la ejecución 1 daba `PASS`). Se restauró `pg.dump` en una base aparte del servidor temporal (`acc119r2_snapshot`). Allí se desactivó el trigger `revision_frozen_immutable` y en el snapshot de la revisión `0ca792c9-…` se cambió la primera coordenada de la línea `entities[3]` de `[2.0, -4.0]` a `[2.01, -4.0]` (1 cm). Se volvió a activar el trigger, se volcó otra vez con `pg_dump --format=custom` y se regeneró `SHA256SUMS`. La comprobación independiente de la ejecución 1 da `recomputed=1f8d865f… MISMATCH` (`Frozen snapshots: 3, SHA-256 matched: 2`). `restore_check.sh` sale ahora con **1** (1,02 s) y nombra la revisión dos veces, contra la columna y contra el evento:

```text
Objects checked: 11 valid
Objects compared with $SCRATCH/live_storage: 11
Sources checked: 3
Originals matched: 3 of 3
Snapshots matched: 2 of 3
Event chains checked: 3
FAIL revision 0ca792c9-104e-45f6-ac29-add19851daec: snapshot SHA-256 is 1f8d865fe21696f569f042daf2d89e3da2f8936f0de0f800bea4334b587aadec, snapshot_sha256 is 14c7f392ee2cde7f52d5ad66f62de9e3bb3c1015ff9f4893544097625e5917d4
FAIL revision 0ca792c9-104e-45f6-ac29-add19851daec: snapshot SHA-256 is 1f8d865fe21696f569f042daf2d89e3da2f8936f0de0f800bea4334b587aadec, revision.frozen event has 14c7f392ee2cde7f52d5ad66f62de9e3bb3c1015ff9f4893544097625e5917d4
FAIL: restore check of $SCRATCH/tampered/snapshot found problems
```

**3. `revision.snapshot_sha256` alterado.** Mismo procedimiento en otra base aparte (`acc119r2_sha`), con `snapshot_sha256 = repeat('0', 64)` en la revisión `0ca792c9-…` y el snapshot intacto. La comprobación independiente da `stored=0000… recomputed=14c7f392… MISMATCH`. `restore_check.sh` sale con **1** (1,03 s). Solo hay discrepancia con la columna, porque el snapshot sigue coincidiendo con el evento:

```text
Objects checked: 11 valid
Objects compared with $SCRATCH/live_storage: 11
Sources checked: 3
Originals matched: 3 of 3
Snapshots matched: 2 of 3
Event chains checked: 3
FAIL revision 0ca792c9-104e-45f6-ac29-add19851daec: snapshot SHA-256 is 14c7f392ee2cde7f52d5ad66f62de9e3bb3c1015ff9f4893544097625e5917d4, snapshot_sha256 is 0000000000000000000000000000000000000000000000000000000000000000
FAIL: restore check of $SCRATCH/tampered/sha found problems
```

Las bases `acc119r2_snapshot` y `acc119r2_sha` se borraron justo después de volcarlas. Al final el servidor temporal solo tenía `postgres`, `sketch2cad`, `template0` y `template1`.

### Pruebas unitarias

```sh
export DATABASE_URL=postgresql+psycopg://sketch2cad:sketch2cad@localhost:5434/sketch2cad_test
uv run pytest -q tests/test_backup_scripts.py
```

Resultado: **5 passed in 6.26s**, sin pruebas omitidas (herramientas `pg_dump`/`pg_restore` del host en `/opt/homebrew/bin`). Incluyen las dos pruebas nuevas, que cambian una coordenada del snapshot y `snapshot_sha256`. No se ejecutaron el resto de la batería ni `ruff` o `pyright`: no forman parte de esta tarea.

### Duraciones

| Paso | Tiempo |
|---|---|
| `docker compose build` (sin OCR, desde caché) | 10 s |
| `up -d --wait` | 13 s |
| Poblado por la API | 10 s |
| `backup.sh` | 0,25 s |
| `restore_check.sh` (respaldo íntegro) | 1,67 s |
| Controles 1a / 1b / 2 / 3 | 1,11 s / 1,01 s / 1,02 s / 1,03 s |
| `pytest tests/test_backup_scripts.py` | 6,3 s |

### Limpieza

- `docker compose -p sdlc-acc-001-119-r2 ... down -v --remove-orphans`: 4 contenedores, la red `sdlc-acc-001-119-r2_default` y los tres volúmenes eliminados.
- `sdlc-acc-001-119-r2-restore-pg` parado (se borró solo, por `--rm`).
- Imágenes `sdlc-acc-001-119-r2-{api,worker,web}:eb60ea5` eliminadas; las `sketch2cad-*:local` no se tocaron.
- Borrados el respaldo, las copias alteradas y la copia de los objetos. No quedó ningún `restore_check.*` en `$TMPDIR` ni ningún puerto 18120/15120 a la escucha.
- `docker ps` antes y después: los mismos 12 contenedores, con la misma imagen y el mismo estado (`sddorchestrator-*`, `sketch2cad-pg-dev`, 8 `textralocal-*` y `magical_lalande`).
- No se hizo ningún commit. El árbol queda como estaba más este informe.

## Qué no cubre esta comprobación

- **Restaurar de verdad y arrancar la aplicación.** No se arrancaron `api` y `worker` contra una base restaurada, ni se comprobó que `alembic upgrade head` no cambie nada ni que la interfaz muestre los expedientes.
- **Artefactos exportados.** Sus ficheros están en `objects.tar` y se verifica su hash, pero no se comprueba que cada fila `artifact` resuelva a su objeto, como con `source`. Tampoco se ejecutó el verificador del paquete sobre el ZIP restaurado.
- **Clave de firma.** El volumen `signing-keys` no forma parte del respaldo. Sin la clave no se pueden firmar paquetes nuevos tras una restauración ni verificar los antiguos sin la pública (docs/running-locally.md).
- **Retención y copia fuera del host.** No se probaron. La política 7/4/12 de docs/backup.md es una sugerencia.
- **Cifrado.** El respaldo no está cifrado y contiene datos personales.
- **Objetos sin fila** que los referencie y **eventos quitados del final de una cadena**: no se detectan (limitaciones conocidas de docs/backup.md y docs/audit.md). Según el código (no se probó), una revisión congelada borrada entera del volcado tampoco se detectaría: `Snapshots matched` cuenta las filas `revision` que hay, y los eventos `revision.frozen` sin fila no se comparan con nada.
- **Carga real y concurrencia.** Instancia pequeña (11 objetos, 1,1 MB, 89 eventos) y en reposo.
- **Linux y producción.** Solo macOS arm64 con Docker Desktop, con la copia por `docker cp` para `STORAGE_ROOT`.
- **Cola de Procrastinate.** Se restaura con el volcado, pero no se comprobó que los trabajos pendientes se reanuden.
