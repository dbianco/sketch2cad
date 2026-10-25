# Ejecutar Sketch2CAD en local con Docker Compose

El fichero `sketch2cad-api/docker-compose.yml` levanta la pila completa con el nombre de proyecto `sketch2cad`. Todos los comandos se ejecutan desde `sketch2cad-api/`.

| Servicio | Imagen | Qué hace | Healthcheck |
|---|---|---|---|
| `db` | `postgres:16`, volumen `db-data` | Base de datos | `pg_isready` por TCP |
| `api` | `sketch2cad-api:local` (`sketch2cad-api/Dockerfile`) | `alembic upgrade head` y después uvicorn en el puerto 8000 | `GET /healthz` con `urllib` de Python |
| `worker` | `sketch2cad-api:local` | Aplica el esquema de Procrastinate (idempotente) y arranca el worker | `python -m sketch2cad.worker.health` |
| `web` | `sketch2cad-web:local` (`sketch2cad-web/Dockerfile`) | nginx con el bundle; reenvía `/api/` a `api:8000` | `HEALTHCHECK` del Dockerfile |

`api` y `worker` esperan a que `db` esté sano; `web` espera a que `api` esté sano. Solo `web` se publica en el host, en `127.0.0.1:${WEB_PORT:-18080}`. `db` y `api` no se publican.

## Variables

Los secretos solo llegan por variables de entorno; el fichero compose no contiene valores literales ni valores por defecto para ellos, y compose se niega a arrancar si falta alguna.

| Variable | Obligatoria | Uso |
|---|---|---|
| `POSTGRES_PASSWORD` | sí | Contraseña del rol `sketch2cad`; con ella se construye `DATABASE_URL`. Solo caracteres válidos en una URL (por ejemplo `openssl rand -hex 24`). |
| `SESSION_SECRET` | sí | Secreto de sesión de la API. |
| `SIGNING_KEY_PATH` | sí | Ruta del fichero de clave de firma dentro del contenedor. Por ahora es solo una ruta: el fichero llega en H4. |
| `APP_ENV` | sí | Entorno de la aplicación. Con `dev` las cookies no llevan `Secure` (necesario sobre HTTP en local). |
| `WEB_PORT` | no | Puerto del host para `web` (18080 si está vacío). |

Para un arranque manual, copia `.env.example` a `.env` (no se versiona) y rellena los valores; compose lee `.env` automáticamente:

```sh
cp .env.example .env    # y rellenar POSTGRES_PASSWORD, SESSION_SECRET, SIGNING_KEY_PATH, APP_ENV
docker compose -p sketch2cad up -d --build --wait
docker compose -p sketch2cad ps
docker compose -p sketch2cad exec api python -m sketch2cad.cli create-user --username <usuario> --role <rol>   # la contraseña se lee por stdin
docker compose -p sketch2cad down -v       # borra también el volumen de la base
```

## Prueba de humo

```sh
bash scripts/compose_smoke.sh
```

El script:

1. Genera en memoria secretos aleatorios de usar y tirar (`openssl rand`), exportados solo para su proceso; nunca se escriben en disco. `APP_ENV` vale `dev` y `SIGNING_KEY_PATH` una ruta de marcador salvo que se definan antes.
2. Se niega a continuar si el proyecto `sketch2cad` ya tiene contenedores.
3. Construye las imágenes (`docker compose build`) e imprime `build_seconds=<n>`.
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
