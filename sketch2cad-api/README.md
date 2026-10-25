# sketch2cad-api

Backend de Sketch2CAD (Python 3.12, FastAPI). Todos los comandos se ejecutan desde esta carpeta.

## Instalar dependencias

```sh
uv sync --frozen
```

## Configuración

La configuración se lee solo de variables de entorno (ver `.env.example`):
`DATABASE_URL`, `SESSION_SECRET`, `SIGNING_KEY_PATH`, `APP_ENV`.

## Tests

```sh
uv run pytest -q
```

## Lint y tipos

```sh
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## Worker (Procrastinate)

Con `DATABASE_URL` definido:

```sh
uv run python -m sketch2cad.worker.app apply-schema   # aplica el esquema de Procrastinate (idempotente)
uv run procrastinate --app=sketch2cad.worker.app.app worker
uv run python -m sketch2cad.worker.health             # 0 si la base responde, 1 si no
```
