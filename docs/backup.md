# Respaldo y prueba de restauración

El MVP se respalda con un `pg_dump` de la base y una copia del directorio de objetos (spec §15 y §17, jotdown H1). Dos scripts de `sketch2cad-api/scripts/` lo automatizan; todos los comandos se ejecutan desde `sketch2cad-api/`.

| Script | Qué hace |
|---|---|
| `backup.sh <carpeta-destino>` | Escribe un respaldo en una subcarpeta con fecha UTC (`<carpeta-destino>/20261025T153000Z/`). |
| `restore_check.sh <carpeta-del-respaldo>` | Restaura el respaldo en una base y un directorio de usar y tirar y verifica los hashes. Nunca escribe en la base ni en el almacenamiento de origen. |

## Qué se respalda

| Fichero | Contenido |
|---|---|
| `pg.dump` | `pg_dump --format=custom` de toda la base (o solo de `BACKUP_SCHEMA`). Incluye expedientes, usuarios, `source`, la tabla `event` con sus cadenas de hashes y la cola de Procrastinate. |
| `objects.tar` | `tar` de `STORAGE_ROOT/objects`: los originales y sus derivados (PNG rasterizados), guardados por contenido en `objects/sha256/ab/cd/<hash>`. `STORAGE_ROOT/tmp` no se copia: solo contiene subidas a medio escribir. |
| `MANIFEST` | Líneas `clave=valor`: `created_at`, `schema`, `objects` (número de ficheros), `pg_mode` (dónde corrió `pg_dump`) y `pg_dump_version`. |
| `SHA256SUMS` | SHA-256 de `pg.dump` y de `objects.tar`. |

Primero se hace el volcado y después el `tar`. Los objetos nunca se modifican ni se borran, así que todo objeto al que apunta una fila del volcado ya existe cuando corre el `tar`; el `tar` puede contener además objetos subidos durante el respaldo, lo que es inofensivo.

## Configuración

| Variable | Script | Uso |
|---|---|---|
| `DATABASE_URL` | ambos | `postgresql[+psycopg]://usuario:contraseña@host:puerto/base`. El sufijo de driver de SQLAlchemy (`+psycopg`) se ignora. Los caracteres especiales de la contraseña van con escape `%XX`. |
| `STORAGE_ROOT` | ambos | En `backup.sh`, la raíz del almacenamiento (por defecto `storage`, como la aplicación). En `restore_check.sh` es opcional: si se define, cada objeto restaurado se compara además con el de origen. |
| `BACKUP_SCHEMA` | `backup.sh` | Volcar solo ese esquema (lo usan las pruebas). Sin definir se vuelca la base entera y la verificación usa `public`. |
| `PG_CONTAINER` | ambos | Contenedor de PostgreSQL en marcha donde ejecutar `pg_dump`/`pg_restore` con `docker exec`. Del URL solo se usan usuario, contraseña y base. |

Dónde corren las herramientas de PostgreSQL, por este orden:

1. En `PG_CONTAINER`, si está definido.
2. Si `DATABASE_URL` apunta a `localhost` o `127.0.0.1`, en el único contenedor en marcha que publica ese puerto (sus herramientas tienen la misma versión que el servidor).
3. En el host (`pg_dump`/`pg_restore` en el `PATH`).

`pg_restore` debe tener al menos la versión del `pg_dump` que escribió el respaldo (ver `pg_dump_version` en `MANIFEST`) y no ser más nuevo que el servidor de destino: un `pg_restore` 18 contra un servidor 16 falla con `SET transaction_timeout`, y un `pg_restore` 16 no lee un volcado de `pg_dump` 18 (`unsupported version (1.16) in file header`). Ejecutar ambos scripts dentro del contenedor evita el problema.

## Hacer un respaldo

En la pila de compose la base no se publica en el host, así que `pg_dump` se ejecuta dentro del contenedor `db`, y el directorio de objetos es el volumen `objects`:

```sh
export DATABASE_URL="postgresql://sketch2cad:${POSTGRES_PASSWORD}@db:5432/sketch2cad"
export PG_CONTAINER="$(docker compose -p sketch2cad ps -q db)"
export STORAGE_ROOT="$(docker volume inspect sketch2cad_objects -f '{{.Mountpoint}}')"   # suele requerir root
bash scripts/backup.sh /ruta/de/respaldos
```

El script termina con `BACKUP_DIR=<carpeta>`. Si falla a mitad, borra la subcarpeta incompleta y sale con código distinto de 0. Para programarlo basta un `cron` o un temporizador de systemd con ese mismo comando. Copia después la subcarpeta fuera del host (otro disco u otra máquina): un respaldo en el mismo disco no protege de su pérdida.

## Prueba de restauración

```sh
bash scripts/restore_check.sh /ruta/de/respaldos/20261025T153000Z
```

El script:

1. Extrae `objects.tar` en un directorio de `mktemp -d`.
2. Crea la base `restore_check_<timestamp>_<pid>_<aleatorio>` junto a la de `DATABASE_URL` (el rol necesita `CREATEDB`) y restaura en ella `pg.dump` con `pg_restore --no-owner --no-privileges --exit-on-error`.
3. Comprueba, con `uv run python`:
   - que `pg.dump` y `objects.tar` coinciden con `SHA256SUMS`;
   - que cada fichero restaurado tiene una ruta `sha256/ab/cd/<hash>` válida y que el SHA-256 de su contenido es su nombre; con `STORAGE_ROOT`, también que coincide con el objeto de origen cuando existe;
   - que cada fila de `source` resuelve a un objeto válido: `storage_key` es la clave de su `sha256` y `raster_key`, si está, existe;
   - que la cadena de hashes de cada expediente y la cadena global de autenticación pasan `sketch2cad.audit.verify.verify_chain` (docs/audit.md).
4. Imprime una línea `FAIL ...` por cada problema, con la ruta del objeto afectado (por ejemplo `FAIL object sha256/a4/d9/a4d9...: content SHA-256 is ...`), y sale con 1. Si todo cuadra imprime `PASS` y sale con 0. Los errores de uso salen con 2.
5. Al salir, pase lo que pase, borra la base y el directorio de usar y tirar.

Como en producción la base no es accesible desde el host, la prueba se hace contra un PostgreSQL temporal, en el mismo host o en otro, con la misma versión mayor:

```sh
docker run -d --rm --name s2c-restore-check -e POSTGRES_USER=sketch2cad \
  -e POSTGRES_PASSWORD=restore -p 127.0.0.1:55432:5432 postgres:16
export DATABASE_URL=postgresql://sketch2cad:restore@localhost:55432/postgres
bash scripts/restore_check.sh /ruta/de/respaldos/20261025T153000Z
docker stop s2c-restore-check
```

## Restaurar de verdad

1. Detener `api` y `worker` (`docker compose -p sketch2cad stop api worker`).
2. Comprobar el respaldo con `restore_check.sh` antes de tocar nada.
3. Crear una base vacía y restaurar: `pg_restore --no-owner --no-privileges --exit-on-error --dbname=<base> < pg.dump` (dentro del contenedor `db` con `docker exec -i`).
4. Extraer los objetos en un `STORAGE_ROOT` vacío: `tar -C "$STORAGE_ROOT" -xf objects.tar`. Si el directorio ya tiene objetos, se pueden extraer encima: un objeto con el mismo nombre tiene el mismo contenido.
5. Arrancar `api` y `worker` apuntando a la base restaurada; `alembic upgrade head` (lo hace `api` al arrancar) no cambia nada si el volcado ya estaba en la última migración.

No se restaura nunca encima de la base en uso: siempre en una base nueva, y después se cambia `DATABASE_URL`.

## Retención (sugerencia)

La spec no fija una política (la retención está diferida, §15). Como punto de partida: un respaldo diario, conservar los 7 últimos diarios, 4 semanales y 12 mensuales, con al menos una copia fuera del host. Ajustarlo con el responsable de los datos.

## Hábito mensual

Una vez al mes, y siempre al cerrar cada hito (spec §17), elegir el último respaldo, ejecutar `restore_check.sh` contra un PostgreSQL temporal y anotar fecha, carpeta y resultado. Un respaldo que no se ha restaurado nunca no cuenta como respaldo.

## Limitaciones

- Los snapshots congelados (revisiones) llegan en H2. Son objetos del mismo almacenamiento, de modo que la comprobación de objetos ya los cubre; cuando existan habrá que añadir que cada revisión resuelva a su objeto, igual que hoy `source`.
- Quitar eventos del final de una cadena no se detecta con los eventos solos (docs/audit.md); lo cubre el manifiesto de H4.
- El respaldo no está cifrado y contiene datos personales: debe guardarse en un disco cifrado y con acceso restringido.
- `pg_dump` y `tar` no forman una instantánea atómica conjunta; es correcto porque los objetos son de solo inserción, pero un objeto subido durante el respaldo puede quedar en `objects.tar` sin su fila.
- `restore_check.sh` no informa de objetos sin fila que los referencie.
- Los scripts pasan `shellcheck` (comprobado con `uvx --from shellcheck-py shellcheck`); no hay un paso de CI que lo ejecute.
