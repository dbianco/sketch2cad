# Ejecutar Sketch2CAD en local con Docker Compose

El fichero `sketch2cad-api/docker-compose.yml` levanta la pila completa con el nombre de proyecto `sketch2cad`. Todos los comandos se ejecutan desde `sketch2cad-api/`.

| Servicio | Imagen | Qué hace | Healthcheck |
|---|---|---|---|
| `db` | `postgres:16`, volumen `db-data` | Base de datos | `pg_isready` por TCP |
| `api` | `sketch2cad-api:local` (`sketch2cad-api/Dockerfile`), volúmenes `objects` y `signing-keys` | `alembic upgrade head` y después uvicorn en el puerto 8000 | `GET /healthz` con `urllib` de Python |
| `worker` | `sketch2cad-api:local`, volúmenes `objects` y `signing-keys` | Aplica el esquema de Procrastinate (idempotente) y arranca el worker | `python -m sketch2cad.worker.health` |
| `web` | `sketch2cad-web:local` (`sketch2cad-web/Dockerfile`) | nginx con el bundle; reenvía `/api/` a `api:8000` | `HEALTHCHECK` del Dockerfile |

`api` y `worker` esperan a que `db` esté sano; `web` espera a que `api` esté sano. Solo `web` se publica en el host, en `127.0.0.1:${WEB_PORT:-18080}`. `db` y `api` no se publican.

## Variables

Los secretos solo llegan por variables de entorno; el fichero compose no contiene valores literales ni valores por defecto para ellos, y compose se niega a arrancar si falta alguna.

| Variable | Obligatoria | Uso |
|---|---|---|
| `POSTGRES_PASSWORD` | sí | Contraseña del rol `sketch2cad`; con ella se construye `DATABASE_URL`. Solo caracteres válidos en una URL (por ejemplo `openssl rand -hex 24`). |
| `SESSION_SECRET` | sí | Secreto de sesión de la API. |
| `SIGNING_KEY_PATH` | sí | Ruta del fichero de clave de firma Ed25519 dentro de los contenedores `api` y `worker`, por ejemplo `/app/keys/signing-key.pem` (volumen `signing-keys`). Ambos se niegan a arrancar si el fichero no existe o si su modo es mayor que 0600, con un mensaje que nombra la ruta. Ver [Clave de firma](#clave-de-firma). |
| `APP_ENV` | sí | Entorno de la aplicación. Con `dev` las cookies no llevan `Secure` (necesario sobre HTTP en local). |
| `WEB_PORT` | no | Puerto del host para `web` (18080 si está vacío). |
| `STORAGE_ROOT` | no | Raíz del almacenamiento de objetos (`objects/` y `tmp/`, que deben estar en el mismo sistema de ficheros). Compose la fija en `/app/storage`, montada sobre el volumen `objects` en `api` y `worker`; fuera de compose vale `storage` bajo el directorio de trabajo. |

Para un arranque manual, copia `.env.example` a `.env` (no se versiona) y rellena los valores; compose lee `.env` automáticamente:

```sh
cp .env.example .env    # y rellenar POSTGRES_PASSWORD, SESSION_SECRET, SIGNING_KEY_PATH, APP_ENV
docker compose -p sketch2cad build
docker compose -p sketch2cad run --rm --no-deps api python -m sketch2cad.cli generate-signing-key --if-missing
docker compose -p sketch2cad up -d --wait
docker compose -p sketch2cad ps
docker compose -p sketch2cad exec api python -m sketch2cad.cli create-user --username <usuario> --role <rol>   # la contraseña se lee por stdin
docker compose -p sketch2cad down -v       # borra también los volúmenes de la base, de los objetos y de la clave
```

## Clave de firma

El manifiesto del paquete final se firma con Ed25519 (esquema `ed25519-jcs-v1`, spec §14.2). La clave privada vive en un fichero PEM PKCS#8 con modo 0600 en `SIGNING_KEY_PATH`; nunca en el repositorio ni en los logs.

- **Generarla**: `python -m sketch2cad.cli generate-signing-key [--path RUTA] [--if-missing]`. Sin `--path` usa `$SIGNING_KEY_PATH`. Crea los directorios que falten (modo 0700) y el fichero con modo 0600 de forma atómica; nunca sobrescribe un fichero existente (sale con código 1). Con `--if-missing` conserva una clave existente válida y sale con 0; si la existente tiene un modo mayor que 0600, falla nombrando la ruta. Imprime `public_key_id` y la clave pública en base64; nunca la clave privada.
- **En compose**: la clave se genera dentro de la imagen, como usuario `app`, en el volumen con nombre `signing-keys` montado en `/app/keys` (directorio con modo 0700 propiedad de `app`). Así no hay problemas de propietario ni de permisos con ficheros del host. `api` y `worker` no la generan solos: si falta, no arrancan (jotdown H4-11), por eso el paso `generate-signing-key --if-missing` va antes de `up`. Los scripts `scripts/compose_smoke.sh` y `sketch2cad-web/scripts/e2e-stack.sh` lo hacen así.
- **Arranque**: `uvicorn --factory sketch2cad.api:create_server_app` y `procrastinate --app=sketch2cad.worker.app.worker_app worker` cargan la clave antes de nada y salen con código 1 y un mensaje de una línea que nombra la ruta si falta `SIGNING_KEY_PATH`, si el fichero no existe, no es un fichero regular, tiene algún bit fuera de lectura/escritura del propietario (0644, 0640, 0604, 0700...; 0400 sí vale) o no es una clave Ed25519.
- **Usar una clave existente**: cópiala al volumen y ajusta propietario y modo, por ejemplo `docker compose -p sketch2cad run --rm --no-deps -v "$PWD/clave.pem:/tmp/clave.pem:ro" api sh -c 'cat /tmp/clave.pem > /app/keys/signing-key.pem && chmod 600 /app/keys/signing-key.pem'`.
- **Copia de seguridad**: `down -v` borra el volumen `signing-keys` y con él la clave; en una instalación real, guarda una copia cifrada fuera del host antes de borrar volúmenes. Sin la clave pública de una instalación no se pueden verificar sus paquetes.

## Prueba de humo

```sh
bash scripts/compose_smoke.sh
```

El script:

1. Genera en memoria secretos aleatorios de usar y tirar (`openssl rand`), exportados solo para su proceso; nunca se escriben en disco. `APP_ENV` vale `dev` y `SIGNING_KEY_PATH` `/app/keys/signing-key.pem` salvo que se definan antes.
2. Se niega a continuar si el proyecto `sketch2cad` ya tiene contenedores.
3. Construye las imágenes (`docker compose build`) e imprime `build_seconds=<n>`. Después genera la clave de firma dentro de la imagen (`generate-signing-key --if-missing`, volumen `signing-keys`); este paso no cuenta en el tiempo de arranque.
4. Ejecuta `docker compose up -d --wait` con las imágenes ya construidas e imprime `elapsed_seconds=<n>`. **El límite de 90 s se aplica a este tiempo**, no al de construcción, que depende de la caché y de la red.
5. Comprueba `GET /` en `web` (200), `GET /api/v1/auth/me` a través del proxy de nginx (401 `application/problem+json`, lo que prueba la cadena web -> api -> db) y `GET /healthz` dentro de `api` con `docker compose exec` (200).
6. Sale con código distinto de 0 si falla cualquier paso o si `elapsed_seconds` supera 90.
7. Al salir siempre ejecuta `docker compose down -v`, salvo con `KEEP_UP=1`.

Con `KEEP_UP=1` la pila queda levantada; para bajarla, compose necesita que las variables obligatorias tengan algún valor:

```sh
POSTGRES_PASSWORD=x SESSION_SECRET=x SIGNING_KEY_PATH=x APP_ENV=x docker compose -p sketch2cad down -v
```

Variables opcionales del script: `WEB_PORT`, `APP_ENV`, `SIGNING_KEY_PATH`, `KEEP_UP`, `MAX_UP_SECONDS`.

## Test de la configuración

`tests/test_compose_config.py` valida el fichero con `docker compose config` (se omite si Docker no está instalado): los cuatro servicios, que solo `web` publica puertos, que las variables obligatorias no tienen valores por defecto y que ninguna contraseña o secreto es un valor literal.
