# Jot Down — Sketch2CAD MVP (Milestones 1 a 7: H0 a H6)

**References:** [prd.md](./prd.md) | [scoping-doc.md](./scoping-doc.md) | Fuente técnica: `docs/specs/Sketch2CAD_MVP.md` v2.0 (secciones citadas como §n)
**Author:** Daniel Oliva
**Date:** 2026-10-24
**Status:** Draft
**Feature del orquestador:** `h0-foundation` (`f_01m5r92x0qvx203ze7xns2edhd`). Las features H1 a H6 se crean después, una por hito, y reutilizan este documento.

Convenciones: el código, los identificadores, los comentarios y los mensajes de log y de excepción van en inglés. Los textos de interfaz van en español. Los documentos van en español.

---

## 0. Corrections

Ninguna. Los repositorios de código aún no existen, así que ninguna afirmación del PRD o del scoping puede contrastarse con código. La pasada de verificación sobre repositorios (paso 10a) no aplica y queda registrada como omitida por esa razón. Hay un cambio de decisión, no una corrección: la cookie de sesión usa `SameSite=Lax` y no `SameSite=Strict` como decía §15 (ya actualizada). Ver decisión D-8.

---

## 1. Architectural Decisions

| ID | Decision | Options Considered | Chosen Approach | Rationale |
|---|---|---|---|---|
| D-1 | Organización del código | Monorepo; dos repositorios | Un solo repositorio (`dbianco/sketch2cad`, el de documentación) con dos carpetas de código: `sketch2cad-api/` (API, worker, migraciones, docker-compose) y `sketch2cad-web/` (editor) | Decisión del dueño (2026-10-25), que reemplaza la de dos repositorios. Un solo historial, un solo CI con filtros por ruta y el contrato OpenAPI se regenera en el mismo commit. |
| D-2 | Tecnología del editor | SVG; Konva | SVG | Decisión del dueño. Se mide 60 fps con 2000 entidades en H0; si falla, migrar a Konva antes de H2. |
| D-3 | Reproducibilidad del DXF | Byte a byte; solo contenido canónico | ⚠️ PENDING DECISION: depende del spike 2 de H0. Por defecto se intenta byte a byte. | Se resuelve con el informe `docs/spikes/dxf-reproducibility.md` al cerrar H0. |
| D-4 | Sesión de usuario | Sesión en servidor + cookie; JWT con refresh | Sesión en servidor (tabla `session`) + cookie `HttpOnly` | Decisión de esta sesión. Revocación inmediata y encaja con el bloqueo tras 10 intentos y el log de auditoría. |
| D-5 | Herramientas | uv/ruff/pytest/pyright; Poetry; pip-tools | Backend: `uv` (lockfile), `ruff`, `pytest`, `pyright`. Frontend: `pnpm`, `eslint`, `vitest`, Playwright | Decisión de esta sesión. El lockfile reproducible es requisito del PDF reproducible. |
| D-6 | Driver de PostgreSQL | psycopg 3; asyncpg | psycopg 3 (LGPL-3.0) | Procrastinate lo exige. Excepción documentada en `license-exceptions.toml` (PRD, DEC-6). |
| D-7 | Generador del cliente TypeScript desde OpenAPI | openapi-typescript; orval; generador propio | `openapi-typescript` (MIT, solo tipos, sin runtime) | Decisión del dueño (2026-10-24). El cliente se genera desde `openapi.json` y CI falla si el resultado difiere del versionado. |
| D-8 | Atributo SameSite de la cookie | Strict; Lax | `Lax` más token CSRF obligatorio en toda mutación | Decisión del dueño. §15 de la spec ya dice `Lax`. Las pruebas H0-13 fijan el comportamiento. |
| D-9 | Dónde corre el E2E | Carpeta web; carpeta api; carpeta raíz | Carpeta `sketch2cad-web/e2e/`, que levanta el compose de `sketch2cad-api/` del mismo commit | Con un solo repositorio ya no hace falta fijar una versión del api (`API_REF` desaparece): el E2E siempre prueba el commit actual. |
| D-10 | Cola de jobs | Broker; PostgreSQL | Procrastinate sobre PostgreSQL 16 | Decisión del scoping. Sin infraestructura adicional. |
| D-11 | Almacenamiento | MinIO; S3; disco | Disco direccionado por contenido detrás de `Storage` | Decisión del scoping. MinIO es AGPL. |
| D-12 | Polling de jobs | WebSocket; polling | Polling cada 2 s a `GET /jobs/{id}` | §9.1. Sin infraestructura adicional. |
| D-13 | Sin topología compartida entre entidades | Grafo de vértices compartidos; geometría propia por entidad | Geometría propia; las conexiones se expresan con `constraint` unidireccional | §8.2, decisión del autor de la spec. |

---

## 2. Overall Solution

Tres procesos Python que comparten un paquete (`sketch2cad`) y una aplicación web.

- **API** (FastAPI, Pydantic v2, SQLAlchemy 2, Alembic): autenticación, expedientes, fuentes, planos, calibración, entidades, revisiones, exportaciones, auditoría, catálogo. Prefijo `/api/v1`. Errores en `application/problem+json` (RFC 9457).
- **Worker** (Procrastinate sobre PostgreSQL): `qc`, `rectify`, `suggest_lines`, `ocr_crop`, `export_dxf`, `export_pdf`, `build_package`. Comparte código y base de datos con la API.
- **Web** (React 18, TypeScript, Vite, Zustand, Immer): editor SVG con `react-zoom-pan-pinch`, pantallas del prototipo (lista de expedientes, carga, calibración, editor, revisión, exportación). Nginx sirve el build.
- **Storage**: interfaz `Storage` con driver `filesystem` direccionado por contenido (`objects/sha256/ab/cd/<hash>`, write-once a nivel de aplicación) y un driver `s3` solo como stub.
- **Verificador** `sketch2cad-verify`: script independiente del servidor dentro de la carpeta `sketch2cad-api/`, que recalcula hashes, cadena de eventos y firma.

Flujo extremo a extremo: el técnico crea un expediente y sube un croquis. El API calcula SHA-256, guarda el original y encola `qc`. El técnico crea un plano, calibra imagen a papel y fija el sistema local. El worker propone polilíneas (`suggested`). El técnico dibuja y reconstruye posiciones por cotas (fijar longitud, triangulación, base más offset). Pasa el plano a revisión; el revisor valida y se congela un snapshot JSON canónico con hash. Las exportaciones generan DXF, PDF técnico, reporte de auditoría y un manifiesto firmado dentro de un ZIP. Cada acción de dominio escribe un evento encadenado por hashes en la misma transacción.

Principios transversales: la IA solo propone (`review_status = suggested`); la cota declarada manda sobre la medida; ningún camino produce un resultado aparentemente confiable por defecto; ningún dato de expediente sale del despliegue (`external_ai.enabled = false`).

Hitos y orden: H0 fundación, H1 expedientes e ingesta, H2 calibración y editor, H3 herramientas métricas, H4 exportación y auditoría, H5 asistencia de IA, H6 endurecimiento y validación interna. H5 puede recortarse; depende de H2 y no bloquea H3 ni H4.

---

## 7. Dependencies (global)

| Dependencia | Uso | Licencia | Hito |
|---|---|---|---|
| Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, psycopg 3 | API y migraciones | MIT, BSD, LGPL (psycopg) | H0 |
| Procrastinate | Cola sobre PostgreSQL | MIT | H0 |
| argon2-cffi | Contraseñas (Argon2id) | MIT | H0 |
| PostgreSQL 16 | Datos, jobs, eventos | PostgreSQL | H0 |
| React 18, Vite, TypeScript, Zustand, Immer, `react-zoom-pan-pinch` | Web | MIT | H0 |
| Pillow, pypdfium2, numpy | Ingesta y rasterizado de PDF | HPND, Apache/BSD | H1 |
| OpenCV (headless), scikit-image, shapely | Calibración, QC, sugerencias | Apache, BSD | H1, H2, H5 |
| sknw o skan | Grafo del esqueleto | BSD | H5 |
| `rfc8785` | JSON canónico | Apache | H1 |
| `cryptography` | Firma Ed25519 | Apache/BSD | H4 |
| ezdxf | DXF | MIT | H4 |
| ReportLab (`invariant=1`) | PDF | BSD | H4 |
| `transformers`, `torch` (CPU), modelo `microsoft/trocr-base-handwritten` | OCR de recortes | Apache, BSD, MIT | H5 |
| hypothesis, Playwright, vitest, `pip-licenses`, `license-checker`, `pip-audit`, `gitleaks` | Pruebas y CI | varias | H0 en adelante |
| FreeCAD (local), LibreCAD y QCAD (CI con `xvfb` donde sea viable) | Verificación del DXF | GPL/LGPL como herramientas externas, no enlazadas | H4 |

Cada licencia LGPL requiere una entrada en `license-exceptions.toml` (paquete, versión, motivo). `torch` se evalúa contra la lista blanca en H5; si alguna dependencia transitiva queda fuera, se documenta antes de añadirla.

## 8. Feature Flag (Split.io)

No aplica. Decisión del dueño: no se usa feature flag. `external_ai.enabled` es una opción de configuración, no un flag de Split.

---

## Milestones

### Milestone 1 of 7 — H0: Fundación operativa (feature `h0-foundation`)

**Objetivo.** Un usuario entra con usuario y contraseña, crea un expediente vacío y lo ve en una lista; CI está verde con chequeo de licencias; dos comprobaciones técnicas dejan decididos el editor y la reproducibilidad del DXF. Cubre REQ-16 y la parte de REQ-01 que no depende de eventos.

#### 3. Database Model Changes (H0)

Base nueva. Migraciones Alembic, cada una con `downgrade` probado.

| Tabla | Campos |
|---|---|
| `user` | `id` UUID PK, `username` text único no nulo, `display_name` text, `password_hash` text, `role` enum (`technician`, `reviewer`, `admin`), `is_active` bool default true, `failed_attempts` int default 0, `locked_until` timestamptz null, `created_at` timestamptz |
| `session` | `id` UUID PK, `user_id` FK, `token_sha256` text único (el token en claro solo existe en la cookie), `csrf_token` text, `created_at`, `expires_at`, `revoked_at` null |
| `case_file` | `id` UUID PK, `code` text único (formato `EXP-<año>-<NNNN>`, secuencia por año), `incident_type` enum (`traffic_collision`, `crime_scene`, `inspection`, `other`), `incident_date` date, `description` text, `declared_location` text, `organization` text null, `status` text default `open`, `created_by` FK, `created_at`. Campos de §8.1 reservados sin lógica: `retention_policy_id` null, `legal_hold` bool default false |
| `case_member` | `case_file_id` FK, `user_id` FK, `permissions` text[]; PK compuesta |

Migración inicial `0001_initial` crea las cuatro tablas. Compatibilidad: no hay datos previos. El usuario administrador inicial se crea con `python -m sketch2cad.cli create-user --username X --role admin` leyendo la contraseña de stdin; no existe usuario por defecto.

#### 4. API Specifications (H0)

Errores: `application/problem+json` con `type`, `title`, `status`, `detail`. Autenticación por cookie `s2c_session`; mutaciones exigen cabecera `X-CSRF-Token` igual al valor de la cookie `s2c_csrf`.

| Endpoint | Request | Response | Errores |
|---|---|---|---|
| `POST /api/v1/auth/login` | `{ "username": str, "password": str }` | 200 `{ "user": { "id", "username", "display_name", "role" } }`; cookies `s2c_session` (HttpOnly, Secure, SameSite=Lax) y `s2c_csrf` | 401 credenciales inválidas (mismo mensaje con usuario inexistente); 423 cuenta bloqueada con `locked_until` |
| `POST /api/v1/auth/logout` | vacío | 204; la sesión queda con `revoked_at` | 401 sin sesión |
| `GET /api/v1/auth/me` | — | 200 el mismo objeto `user` | 401 |
| `POST /api/v1/case-files` | `{ "incident_type", "incident_date", "description", "declared_location" }` | 201 el `case_file` con `code` | 401; 403 sin CSRF; 422 validación |
| `GET /api/v1/case-files` | `?q=&incident_type=` | 200 lista de los expedientes donde el usuario es miembro o creador | 401 |
| `GET /api/v1/case-files/{id}` | — | 200 `case_file` | 401; 404 si no es miembro (no se distingue de inexistente) |
| `GET /healthz` | — | 200 `{ "status": "ok", "db": "ok" }` | 503 si la base no responde |

Reglas de autenticación (valores configurables con estos defaults): Argon2id con los parámetros por defecto de `argon2-cffi`; bloqueo de 15 minutos tras 10 intentos fallidos consecutivos; sesión con expiración por inactividad de 8 horas (`auth.session_ttl_minutes = 480`); `Secure` desactivable solo con `APP_ENV=dev`.

Criterios por endpoint:
- `login` con credenciales correctas devuelve 200 y cookies con los atributos indicados; con 10 contraseñas incorrectas seguidas la solicitud 11 devuelve 423 aunque la contraseña sea correcta.
- `POST /case-files` sin cabecera `X-CSRF-Token` devuelve 403.
- `GET /case-files/{id}` de un expediente ajeno devuelve 404.

#### 5. System Design (H0)

Carpeta `sketch2cad-api/`: `src/sketch2cad/{api,auth,cases,db,storage,worker,config}`, `migrations/`, `tests/`, `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `uv.lock`, `licenses-allowed.txt`, `license-exceptions.toml`. Los workflows viven en la raíz del repositorio (`.github/workflows/api-ci.yml`, `web-ci.yml`, `e2e.yml`) con filtros `paths:`; los informes de spikes en `docs/spikes/` de la raíz.
Carpeta `sketch2cad-web/`: `src/{app,pages,editor,api}`, `spikes/svg-perf/`, `e2e/`, `package.json`, `pnpm-lock.yaml`, `Dockerfile` (build + nginx).
`docker-compose.yml` (en `sketch2cad-api/`): servicios `db` (postgres:16, volumen nombrado), `api` (uvicorn), `worker` (`procrastinate worker`, sin tareas todavía), `web` (build desde `../sketch2cad-web`). Todos con `healthcheck`. Secretos solo por variables de entorno (`DATABASE_URL`, `SESSION_SECRET`, `SIGNING_KEY_PATH`), con `.env.example` sin valores reales.
Pantallas H0 (prototipo): inicio de sesión y lista de expedientes con formulario "Nuevo expediente". El cliente TypeScript se genera desde `openapi.json` (decisión D-7).

#### Spike 1 — medición de 60 fps (web)
`spikes/svg-perf` genera 2000 entidades sintéticas (mezcla de 1200 polilíneas de 4 a 12 vértices, 600 líneas y 200 símbolos) en un `<svg>`, ejecuta un recorrido de pan y zoom programático de 10 segundos y escribe `spikes/svg-perf/result.json` con `median_fps`, `p95_frame_ms`, `entities`, `browser`, `hardware`. El resultado y la decisión (mantener SVG o migrar a Konva) se registran en `docs/spikes/svg-performance.md`.

#### Spike 2 — reproducibilidad del DXF (api)
Script `spikes/dxf_repro.py` genera con ezdxf un DXF R2013 de un plano fixture (una polilínea, un texto, un símbolo con atributo, una cota), en dos procesos distintos con 2 segundos de diferencia, y compara los bytes. Documenta en `docs/spikes/dxf-reproducibility.md` cuáles campos difieren (`$TDCREATE`, `$TDUPDATE`, `$FINGERPRINTGUID`, `$VERSIONGUID`, handles) y qué ajustes los fijan. Resuelve la decisión D-3.

#### 6. Service Interactions (H0)
API → PostgreSQL (SQLAlchemy). Worker → PostgreSQL (Procrastinate). Web → API por HTTPS y cookies; en desarrollo, proxy de Vite hacia `api`.

#### 9. Deployment Stages (H0)
1. Crear las carpetas `sketch2cad-api/` y `sketch2cad-web/` en el repositorio existente `dbianco/sketch2cad` (primer commit con la estructura; `record_commit` tras cada commit).
2. Compose local: `docker compose up` levanta `db`, `api`, `worker`, `web`.
3. CI en ambos repositorios. Rollback: `git revert`; la base local se recrea con `docker compose down -v`.
Riesgo: los dos CI comparten el repositorio y se disparan en cada cambio; mitigación: filtros `paths:` en cada workflow y un job de contrato que corre cuando cambia cualquiera de las dos carpetas.

#### 10. Testing Strategy y Acceptance Criteria (H0)

| ID | Criterio medible | Prueba |
|---|---|---|
| H0-1 | Con el compose levantado, `GET /healthz` responde 200 y los cuatro servicios están `healthy` en menos de 90 s desde `docker compose up`. | Script `scripts/compose_smoke.sh` en CI que mide el tiempo y falla si lo supera. |
| H0-2 | El login responde en menos de 300 ms en p95 sobre 20 solicitudes seguidas con credenciales válidas, medido en la máquina de desarrollo; en CI se mide y se reporta. | Test de integración con `pytest` que mide y compara. |
| H0-3 | Con 10 contraseñas incorrectas seguidas, la solicitud 11 devuelve 423 con credenciales correctas; después de `locked_until` vuelve a responder 200. | Integración con reloj controlado. |
| H0-4 | `POST /case-files` crea un expediente con `code` único formato `EXP-<año>-<NNNN>`; dos creaciones simultáneas no repiten código. | Integración con dos hilos. |
| H0-5 | `alembic upgrade head` seguido de `alembic downgrade base` termina sin error y deja el esquema vacío en una base limpia. | Job de CI. |
| H0-6 | La duración del CI completo de cada repositorio se mide y se reporta en cada ejecución; la referencia es 10 minutos y superarla no bloquea. Si se supera en 3 ejecuciones seguidas, se abre una tarea de optimización. | Resumen del workflow con la duración; revisión al cierre del hito. |
| H0-7 | CI falla si se añade una dependencia fuera de la lista blanca (MIT, BSD, Apache-2.0, PSF, LGPL con excepción documentada). | Test del propio job: rama de prueba con un paquete GPL de ejemplo que debe fallar. |
| H0-8 | CI falla ante un hallazgo de severidad alta de `pip-audit` o `pnpm audit --audit-level=high` no listado en `audit-exceptions.toml` con justificación. | Rama de prueba con una dependencia vulnerable conocida. |
| H0-9 | `gitleaks` falla si el repositorio contiene una clave privada. | Fixture con una clave falsa en un test, que debe detectarse; el escaneo normal la excluye por ruta. |
| H0-10 | Spike 1: con 2000 entidades, `median_fps ≥ 60` y `p95_frame_ms ≤ 25` en el portátil de desarrollo; el informe lista el hardware. Si no se cumple, el informe recomienda Konva y H2 no empieza. | `pnpm spike:svg` y el archivo `result.json`. |
| H0-11 | Spike 2: el informe indica si dos exportaciones del fixture producen el mismo SHA-256 y, si no, lista cada campo distinto y el ajuste que lo corrige; con los ajustes aplicados, el hash coincide o se documenta por qué no. | `spikes/dxf_repro.py` y el informe. |
| H0-13 | La cookie `s2c_session` se emite con `HttpOnly`, `Secure` y `SameSite=Lax`; un `POST`, `PUT`, `PATCH` o `DELETE` sin `X-CSRF-Token` válido devuelve 403, aunque la cookie de sesión sea válida; `GET /auth/me` y `GET /case-files` funcionan sin token. | Integración que inspecciona `Set-Cookie` y prueba cada verbo. |
| H0-12 | Test E2E con Playwright: iniciar sesión, crear un expediente y verlo en la lista, contra el stack real (api, worker, db, web), no contra `vite preview`. | `e2e/login_case.spec.ts` en CI. |

Harness: el E2E debe ejecutarse contra el compose completo; un servidor estático no puede probar login ni creación. La tarea de CI "levantar el compose de `sketch2cad-api/` del mismo commit" es parte de H0 (decisión D-9). Pruebas unitarias del api con `pytest` y base real (servicio PostgreSQL de CI); pruebas del web con `vitest`.

---

### Milestone 2 of 7 — H1: Expedientes e ingesta

**Objetivo.** Un técnico sube un croquis, ve su SHA-256 y el semáforo de calidad, otro usuario sin autorización no puede verlo, y existe un log de eventos encadenado. Cubre REQ-01 (permisos), REQ-02, REQ-11 y CA-01, CA-12 parcial. Depende de H0.

#### 3. Database Model Changes (H1)

| Tabla | Cambio |
|---|---|
| `source` (nueva) | `id` UUID, `case_file_id` FK, `original_filename`, `media_type` (`image/jpeg`, `image/png`, `image/tiff`, `application/pdf`), `byte_size` bigint, `sha256` char(64), `storage_key`, `width_px`, `height_px`, `dpi` numeric null, `exif` JSONB, `qc` JSONB (`status` green/amber/red, `checks[]` con `name`, `status`, `value`, `threshold`, `message`), `uploaded_by` FK, `uploaded_at` |
| `event` (nueva) | `id` bigserial, `case_file_id` FK, `plan_id` null, `revision_id` null, `actor_id` null, `actor_role`, `kind` text, `entity_id` null, `before` JSONB, `after` JSONB, `reason` null, `client_ip`, `occurred_at`, `prev_hash` char(64), `hash` char(64). Índice único `(case_file_id, id)`. Trigger que rechaza `UPDATE` y `DELETE` sobre `event`. |
| `job_meta` (nueva) | `id`, `plan_id` null, `source_id` null, `kind`, `params` JSONB, `status`, `result_ref`, `error`, `model_version`, `started_at`, `finished_at` |
| `case_member` | Se usa: `permissions` incluye `read`, `edit`, `deliver` |

Encadenamiento: `hash = SHA-256(prev_hash || JCS(event sin hash))`; el primero de cada expediente usa `prev_hash = SHA-256(case_file_id)`. Escritura en la misma transacción que el cambio, con bloqueo de fila del expediente para serializar la cadena. Los eventos de H0 (creación de expedientes y logins) no existen; la migración no los retrocede.

#### 4. API Specifications (H1)

| Endpoint | Request | Response | Errores |
|---|---|---|---|
| `POST /case-files/{id}/members` | `{ "user_id", "permissions": ["read","edit","deliver"] }` | 201 el miembro | 403 sin `admin` ni creador |
| `POST /case-files/{id}/sources` (multipart) | campos `file`, `client_sha256` | 201 `source` con `qc` | 413 > 50 MB; 415 tipo inválido por magic bytes; 422 `client_sha256` distinto del calculado, con `detail` "El archivo se alteró durante la subida. Vuelve a cargarlo." |
| `GET /sources/{id}/original` | — | 200 bytes originales; encabezado `X-Content-SHA256`; escribe evento `source.downloaded` | 403 sin permiso `read`, con evento `access.denied` |
| `GET /sources/{id}/preview?max=2048` | `max` entre 256 y 4096 | 200 imagen JPEG derivada, lado mayor ≤ `max` | 403 |
| `GET /case-files/{id}/events` | `?plan_id=&kind=` | 200 lista paginada con `prev_hash` y `hash` | 403 |

Reglas: PDF de una sola página se rasteriza a 300 dpi en el worker (pypdfium2) y nunca se interpreta en el navegador; tipos permitidos JPEG, PNG, TIFF y PDF por contenido. El `original` solo se sirve desde el almacenamiento sin transformar. El QC corre en el worker (`qc`) y el cliente consulta el estado con `GET /jobs/{id}` cada 2 s.

QC (valores por defecto configurables, §10): lado corto < 1500 px ámbar y < 900 px rojo; varianza del Laplaciano < 100 ámbar y < 40 rojo; contraste p95−p5 < 60 niveles ámbar; la advertencia no bloquea el avance.

Eventos H1: `case_file.created`, `member.added`, `source.ingested`, `source.downloaded`, `auth.login`, `auth.failed`, `access.denied`.

#### 5. System Design (H1)
Módulo `storage`: `Storage.put(stream) -> (sha256, key)`, `get(key)`, `exists(key)`. El driver `filesystem` escribe en archivo temporal, calcula SHA-256, mueve a `objects/sha256/ab/cd/<hash>` con permisos de solo lectura y no sobrescribe si existe. Modelo de amenaza: la protección es de aplicación (la API no expone reemplazo ni borrado); quien tenga acceso al volumen o al host puede alterar objetos, y eso se detecta por hash en el manifiesto y en `sketch2cad-verify`, no se impide. Módulo `audit`: `append_event(session, ...)`. Dependencia FastAPI `require_case_permission("read"|"edit"|"deliver")`. Pantallas: carga de fuente con control de calidad (prototipo 2) y visor de fuente (zoom y pan con `react-zoom-pan-pinch`).

#### 9. Deployment Stages (H1)
Migración `0002_sources_events`; rollback con `downgrade`. Nuevo volumen `objects` en el compose; respaldo con `pg_dump` más copia del directorio, documentado en `docs/backup.md` con prueba de restauración manual al cierre del hito.

#### 10. Testing Strategy y Acceptance Criteria (H1)

| ID | Criterio medible | Prueba |
|---|---|---|
| H1-1 (CA-01) | Un archivo subido y luego descargado tiene el mismo SHA-256 que el registrado y que el `client_sha256`. | Integración: carga, descarga, compara. |
| H1-2 | Un archivo con `client_sha256` distinto devuelve 422 y no deja objeto en el almacenamiento. | Integración. |
| H1-3 | Un archivo de 50 MB + 1 byte devuelve 413; un `.jpg` con contenido de texto devuelve 415. | Integración. |
| H1-4 | El QC marca rojo una imagen de lado corto 800 px, rojo una de varianza del Laplaciano 30, ámbar una de lado corto 1200 px, verde una nítida de 3000 px. | Unitario con imágenes sintéticas generadas con OpenCV. |
| H1-5 (CA-12 parcial) | Un usuario sin membresía recibe 403 en lectura, descarga y listado de eventos; cada intento crea un evento `access.denied`. | Integración con dos usuarios. |
| H1-6 | Modificar un byte de cualquier evento hace que `verify_chain(case_file_id)` devuelva el id del primer evento inválido. | Unitario y propiedad con hypothesis. |
| H1-7 | `UPDATE` o `DELETE` sobre `event` falla con error de base de datos. | Integración. |
| H1-8 | Dos escrituras simultáneas de eventos en el mismo expediente producen una cadena sin huecos ni bifurcaciones. | Integración con 2 hilos y 100 eventos cada uno. |
| H1-9 | Los logs de aplicación no contienen bytes de imagen ni coordenadas, solo identificadores. | Test que captura logs durante la ingesta y busca el nombre del archivo y los datos de prueba. |
| H1-11 | La API no expone ninguna ruta de reemplazo ni de borrado de objetos (el esquema OpenAPI no contiene `PUT` ni `DELETE` sobre `/sources/{id}/original`); `Storage.put` con un hash existente no modifica el archivo (misma fecha de modificación y mismo contenido). | Integración sobre `openapi.json` y unitario del driver. |
| H1-10 | E2E: iniciar sesión, subir un croquis sintético, ver el semáforo y abrir el visor. | Playwright contra el stack real. |

---

### Milestone 3 of 7 — H2: Calibración y editor

**Objetivo.** El técnico crea un plano desde una fuente, calibra la imagen, fija el sistema local, dibuja y edita con deshacer y rehacer, usa símbolos del catálogo y guarda revisiones. Cubre REQ-03, REQ-04, REQ-05, REQ-09, REQ-10 y CA-02, CA-10. Depende de H1 y del resultado del spike 1.

#### 3. Database Model Changes (H2)

| Tabla | Campos |
|---|---|
| `plan` | `id`, `case_file_id` FK, `title`, `primary_source_id` FK, `source_mode` enum (`scaled_drawing`, `schematic_sketch`; default `schematic_sketch`), `status` enum (`CREATED`, `CALIBRATION_PENDING`, `EDITING`, `REVIEW_PENDING`, `IN_REVIEW`, `VALIDATED`, `EXPORTED`, `REJECTED`, `REOPENED`), `metric_status` enum (`metric`, `approximate`, `non_metric`), `current_revision_id`, `validated_revision_id` null, `edit_lease` JSONB null (`user_id`, `expires_at`), `created_at` |
| `calibration` | campos de §8.1: `method` (`scan_dpi`, `scan_segment`, `photo_homography_4pt`, `none`), `image_to_paper` (3×3), `control_points` JSONB, `reprojection_error_mm` (`max`, `rms`), `paper_scale` null, `local_frame` JSONB (`origin_paper_mm`, `x_axis_angle_deg`, `north_angle_deg`), `accepted_by`, `accepted_at` |
| `revision` | `id`, `plan_id`, `number`, `parent_revision_id` null, `state` (`draft`, `frozen`), `snapshot` JSONB null, `snapshot_sha256` null, `created_by`, `created_at`, `frozen_at` |
| `entity` | esquema de §8.2: `id`, `revision_id`, `type`, `layer`, `geometry` JSONB, `image_region` JSONB null, `attributes` JSONB, `evidence_id` null, `constraint` JSONB null, `source` JSONB, `confidence` float null, `model_version` null, `review_status`, `metric_origin`, `created_by`, `updated_at`, `deleted_at` null |
| `catalog` | `id`, `version`, `is_active`, `layers` JSONB, `symbols` JSONB, `created_at`; semilla `default-1` con las 10 capas de §12.1 y los 8 símbolos de §12.3 (`north_arrow`, `evidence_marker`, `control_point`, `door`, `window`, `generic_vehicle`, `traffic_light`, `tree`), sin símbolos de víctimas, sangre, armas ni casquillos |
| `artifact` | `id`, `plan_id`, `revision_id`, `kind`, `is_draft`, `sha256`, `storage_key`, `byte_size`, `generator_version`, `created_at` (se usa en H2 para `rectified_image`) |

Restricciones: una sola calibración activa por plano; `entity.deleted_at` en lugar de borrado físico mientras la revisión es `draft`.

#### 4. API Specifications (H2)

| Endpoint | Request | Response |
|---|---|---|
| `POST /case-files/{id}/plans` | `{ "title", "primary_source_id", "source_mode" }` | 201 plano en `CALIBRATION_PENDING`; no encola ningún job de IA |
| `GET /plans/{id}` | — | 200 plano con calibración, lease y revisión actual |
| `PUT /plans/{id}/calibration` | `{ "method", "control_points": [{"image_px":[x,y], "paper_mm":[x,y]}], "known_dimensions_mm": {"width", "height"}, "paper_scale": int|null, "segment_mm": number|null }` | 200 `{ "image_to_paper": [[...]], "reprojection_error_mm": {"max","rms"}, "meets_tolerance": bool }`. No persiste hasta aceptar. |
| `POST /plans/{id}/calibration/accept` | `{ "as_metric": bool }` | 200; 409 `V-08` si hay entidades; 422 si `as_metric` y el error supera `calibration.max_reprojection_error_mm`; genera `rectified_image` y pasa a `EDITING` |
| `PUT /plans/{id}/local-frame` | `{ "origin_paper_mm": [x,y], "x_axis_angle_deg": n, "north_angle_deg": n }` | 200; si hay entidades, aplica transformación rígida en una transacción y un evento `local_frame.changed` |
| `POST /plans/{id}/lease`, `DELETE /plans/{id}/lease` | — | 200 lease de 10 minutos renovable; 409 con `holder` si lo tiene otro usuario |
| `GET /revisions/{id}/entities`, `POST /revisions/{id}/entities` | entidades según §8.2 | 200 y 201 |
| `PATCH /entities/{id}`, `DELETE /entities/{id}` | `{ "geometry"?, "attributes"?, "layer"?, "operation": "move_vertex"|"snap_orthogonal"|"snap_endpoint"|"snap_grid"|"relabel"|"reclassify", "reason"? }` | 200; rechaza 409 si la revisión está `frozen` o el usuario no tiene el lease |
| `POST /plans/{id}/transition` | `{ "to", "reason"? }` | 200 o 409 con la transición permitida |
| `GET /catalog/active` | — | 200 capas y símbolos (SVG inline) |

Máquina de estados H2: `CREATED → CALIBRATION_PENDING → EDITING → REVIEW_PENDING → IN_REVIEW → VALIDATED`, más `REJECTED → EDITING`, `REOPENED → EDITING`, con los permisos de §9.2 (técnico: calibrar y enviar a revisión; revisor: tomar, validar, rechazar, reabrir). Rechazar exige `reason`. `VALIDATED` congela: serializa el snapshot canónico (§8.3, RFC 8785), calcula `snapshot_sha256` y fija `validated_revision_id`. Reabrir crea una revisión `draft` con `parent_revision_id` y deja la anterior intacta.

Transformaciones: imagen→papel (homografía 3×3 con `cv2.findHomography` sobre 4 puntos y `cv2.perspectiveTransform`; o escala uniforme por DPI o por segmento); papel→mundo solo en `scaled_drawing` (`m = mm × paper_scale / 1000`); ejes: imagen y papel con Y hacia abajo, mundo con Y hacia arriba. Rectificado a 10 px/mm con `cv2.warpPerspective` en el worker (`rectify`).

Eventos H2: `plan.created`, `calibration.proposed`, `calibration.accepted`, `local_frame.set`, `local_frame.changed`, `entity.created`, `entity.updated`, `entity.deleted`, `plan.transition`, `revision.frozen`, `lease.acquired`, `lease.released`.

#### 5. System Design (H2)
Frontend: estado en Zustand; entidades en un store normalizado; deshacer y rehacer con parches de Immer (`produceWithPatches`) y pila local; cada operación se envía al API con `operation` y se refleja en el log. Componentes: `EditorCanvas` (SVG, capas por `layer`), `Palette`, `LayerPanel`, `PropertiesPanel`, `CalibrationWizard` (escaneo con DPI, escaneo con segmento, fotografía de 4 puntos), `LocalFrameTool`, `SymbolPicker`, `LeaseBanner`, `MetricStatusBadge`. Snapping a extremos, ortogonal (0° y 90° respecto de los ejes locales) y a grilla configurable; cada aplicación se registra como evento con usuario y entidad. Accesibilidad: controles interactivos con etiqueta y operables por teclado (WCAG 2.1 AA).

#### 9. Deployment Stages (H2)
Migración `0003_plans_editor`. Seed de catálogo `default-1` en la migración de datos. Rollback con `downgrade`; las revisiones `frozen` son inmutables por trigger (`UPDATE` rechazado salvo `state` de `draft` a `frozen` una vez).

#### 10. Testing Strategy y Acceptance Criteria (H2)

| ID | Criterio medible | Prueba |
|---|---|---|
| H2-1 (CA-02) | Una fotografía sintética de un patrón de 200 × 150 mm generada con `cv2.warpPerspective` y rectificada con 4 puntos da distancias en papel dentro de 1 mm o 1 %, el mayor; el error de reproyección máximo informado es ≤ 1,0 mm. | Fixture sintético y test de la inversa. |
| H2-2 | `calibration/accept` con `as_metric = true` y error de reproyección mayor que 1,0 mm devuelve 422; con `method = none` el plano queda `non_metric`. | Integración. |
| H2-3 | Una fotografía sin rectificar no puede marcarse métrica: el endpoint lo rechaza. | Integración. |
| H2-4 | Con una entidad aceptada o manual, `PUT /calibration` devuelve 409 con el mensaje de V-08. | Integración. |
| H2-5 | Cambiar origen o eje +X con 50 entidades las transforma rígidamente (distancias entre pares iguales dentro de 1e-9 m) y genera exactamente un evento `local_frame.changed`. | Unitario y propiedad con hypothesis. |
| H2-6 | Cambiar el Norte no modifica ninguna coordenada de entidades. | Unitario. |
| H2-7 (CA-10) | Cada transición no incluida en §9.2 devuelve 409; `VALIDATED` rechaza `PATCH` de entidades con 409; reabrir crea la revisión `n+1` con `parent_revision_id` y deja la `n` con el mismo `snapshot_sha256`. | Integración sobre la máquina de estados. |
| H2-8 | El snapshot validado recuperado después coincide byte a byte con el almacenado. | Integración. |
| H2-9 | Un segundo usuario que pide el lease mientras otro lo tiene recibe 409 con el nombre del titular; el lease vence a los 10 minutos sin renovar. | Integración con reloj controlado. |
| H2-10 | Deshacer tras mover un vértice restaura las coordenadas exactas (igualdad de arreglos, sin tolerancia). | Unitario del store y E2E. |
| H2-11 | E2E: abrir un plano de ejemplo, usar cada herramienta de dibujo (línea, polilínea, punto, texto, símbolo, mover vértice, eliminar, reclasificar capa, medir) y comprobar el resultado en el estado y en el log. | Playwright contra el stack real. |
| H2-12 | Con 2000 entidades el editor mantiene `median_fps ≥ 60` y `p95_frame_ms ≤ 25` en el portátil de desarrollo. | Mismo script del spike 1 sobre el editor real. |
| H2-13 | El catálogo `default-1` tiene 10 capas y 8 símbolos y no contiene ningún símbolo con `symbol_id` de víctima, sangre, arma o casquillo. | Unitario sobre el seed. |
| H2-14 | El editor completa un recorrido con solo teclado (tabulador y atajos documentados) para crear una línea, y `axe-core` no reporta violaciones críticas ni serias en la pantalla del editor. | Playwright con `@axe-core/playwright`. |

---

### Milestone 4 of 7 — H3: Herramientas métricas

**Objetivo.** El técnico reconstruye posiciones a partir de cotas: fija la longitud de un segmento, ubica un indicio por triangulación o por base más offset, numera los indicios por expediente y ve el estado métrico. Cubre REQ-06, REQ-07, REQ-08 y CA-03. Depende de H2.

#### 3. Database Model Changes (H3)

| Tabla | Cambio |
|---|---|
| `evidence` (nueva) | `id`, `case_file_id` FK, `number` int (único por expediente, asignado por secuencia del expediente), `label`, `attributes` JSONB, `created_by`, `created_at` |
| `entity` | `evidence_id` pasa a FK a `evidence`; `constraint` JSONB con `kind` (`triangulation`, `baseline_offset`), `refs[]`, `params` (`d_a_m`, `d_b_m`, `solution`, o `s_m`, `d_m`, `side`) |
| `plan.metric_status` | Se recalcula en cada cambio |

Eventos nuevos: `evidence.created`, `evidence.updated`, `entity.recomputed`.

#### 4. API Specifications (H3)

| Endpoint | Request | Response |
|---|---|---|
| `GET/POST /case-files/{id}/evidence`, `PATCH /evidence/{id}` | `{ "label", "attributes" }` | 201 con `number` asignado |
| `PATCH /entities/{id}` con `operation = "fix_length"` | `{ "anchor_vertex": int, "value_m": number }` | 200; mueve el vértice libre y, rígidamente, los vértices del mismo lado; `metric_origin = declared_dimension` |
| `PATCH /entities/{id}` con `operation = "triangulate"` | `{ "ref_a": id, "ref_b": id, "d_a_m": n, "d_b_m": n, "solution": "nearest"|"alt" }` | 200 con las dos soluciones candidatas y la elegida; 422 con causa si las circunferencias no se cruzan (V-06) o si una referencia no es métrica o depende de la entidad (V-07); nada se mueve en caso de error |
| `PATCH /entities/{id}` con `operation = "baseline_offset"` | `{ "base_a": id, "base_b": id, "s_m": n, "d_m": n, "side": "left"|"right" }` | 200 con `P = A + s·û + d·n̂`; mismas reglas V-07 |
| `POST /revisions/{id}/entities` con `type = "dimension"` | `{ "p1_m", "p2_m", "offset_m", "declared_value_m" }` | 201 con `measured_value_m` y `discrepancy` |
| `GET /plans/{id}/metric-status` | — | 200 `{ "status", "blocking_entities": [{ "id", "layer", "metric_origin" }] }` |

Reglas (§11.3): `fix_length` sobre un segmento AB con ancla A mueve B y los vértices del mismo lado de la polilínea. Triangulación y base más offset guardan su `constraint`; si una referencia se mueve, la entidad dependiente se recalcula y se emite `entity.recomputed`. Mover a mano una entidad con `constraint` pide confirmación, elimina la restricción y deja `metric_origin = none`. `discrepancy = true` cuando `|declared_value_m − measured_value_m| > dimension.discrepancy_tolerance_m` (0,02 m por defecto); el sistema nunca corrige ninguno de los dos valores. Un plano es `metric` solo si todas las entidades en `L_STRUCTURE` y `L_ACCESS` y las que referencian un indicio tienen `metric_origin` en {`declared_dimension`, `triangulation`, `baseline_offset`, `scaled_drawing`, `derived`}; en caso contrario es `approximate`; sin calibración es `non_metric`. Una polilínea es `declared_dimension` solo si todos sus segmentos tienen longitud declarada. Las referencias de una restricción no pueden depender directa ni indirectamente de la entidad que se fija (detección de ciclos en el grafo de `constraint`).

#### 5. System Design (H3)
Módulo `geometry` sin dependencias de FastAPI: `circle_intersections(a, ra, b, rb) -> list[Point]`, `baseline_offset_point(a, b, s, d, side) -> Point`, `scale_from_anchor(polyline, vertex_index, anchor_index, target_length)`, `has_dependency_cycle(entity_id, constraints)`, `compute_metric_status(entities, calibration)`. El frontend repite el cálculo para la vista previa, pero el servidor es la autoridad. Componentes: `TriangulationDialog`, `BaselineOffsetDialog`, `FixLengthTool`, `EvidenceTable`, `MetricStatusPanel` con la lista de entidades que impiden `metric`.

#### 9. Deployment Stages (H3)
Migración `0004_evidence_constraints`. Rollback con `downgrade`. Las revisiones existentes sin `constraint` siguen válidas.

#### 10. Testing Strategy y Acceptance Criteria (H3)

| ID | Criterio medible | Prueba |
|---|---|---|
| H3-1 (CA-03) | En un croquis esquemático con cotas conocidas, las coordenadas de un indicio por triangulación y por base más offset coinciden con las calculadas a mano dentro de 1 cm, en al menos 5 casos resueltos a mano. | Unitario con casos y propiedad con hypothesis (distancia a cada referencia = distancia declarada ± 1e-9 m). |
| H3-2 | Circunferencias tangentes devuelven una solución; disjuntas o concéntricas devuelven cero y 422; ninguna entidad cambia. | Unitario de casos degenerados e integración. |
| H3-3 | Una referencia con `metric_origin = trace_only` o `none` rechaza la operación con el mensaje de V-07. | Integración. |
| H3-4 | Una restricción que crea un ciclo (A depende de B y B de A) es rechazada. | Unitario. |
| H3-5 | Al mover una referencia, la entidad dependiente se recalcula y existe un evento `entity.recomputed` con `before` y `after`. | Integración. |
| H3-6 | `fix_length` de 4,20 m sobre un segmento de 3,00 m deja la longitud en 4,20 m ± 1e-9 m y desplaza el resto de la polilínea por el mismo vector. | Unitario. |
| H3-7 | Una cota declarada de 4,20 m con medida 4,24 m queda con `discrepancy = true`; con medida 4,215 m queda `false`; ningún valor se modifica. | Unitario. |
| H3-8 | Un plano con una entidad `trace_only` en `L_STRUCTURE` es `approximate`; al reemplazarla por una con `declared_dimension` pasa a `metric`; sin calibración es `non_metric`. | Unitario de `compute_metric_status`. |
| H3-9 | El mismo indicio aparece con el mismo número en dos planos del mismo expediente; dos expedientes distintos pueden repetir números. | Integración. |
| H3-10 | E2E: ubicar un indicio por triangulación en un plano de ejemplo y ver la tabla de indicios con coordenadas locales. | Playwright contra el stack real. |

---

### Milestone 5 of 7 — H4: Exportación y auditoría

**Objetivo.** Un revisor valida un plano y descarga un paquete firmado (DXF, PDF técnico, reporte de auditoría, manifiesto, ZIP); un auditor externo lo verifica sin acceso al servidor. Cubre REQ-12, REQ-13, REQ-14, REQ-15 y CA-06 a CA-09. Depende de H3 y del resultado del spike 2.

#### 3. Database Model Changes (H4)
`artifact` se usa para `dxf`, `pdf_plan`, `pdf_audit`, `manifest`, `zip` (con `is_draft`). Nuevo evento `export.requested`, `artifact.created`, `artifact.downloaded`. Migración `0005_exports` solo si faltan índices: `artifact (plan_id, revision_id, kind)`. Estado `EXPORTED` del plano. No hay cambios de columnas.

#### 4. API Specifications (H4)

| Endpoint | Request | Response |
|---|---|---|
| `POST /plans/{id}/exports` | `{ "mode": "draft"|"final", "paper": "A4"|"A3", "scale": 50|100|200|250|500|null }` | 202 `job`; `final` exige estado `VALIDATED` y permiso `deliver` (V-11); `scale = null` usa la sugerencia automática |
| `GET /plans/{id}/artifacts` | — | 200 lista con `kind`, `is_draft`, `sha256`, `byte_size` |
| `GET /artifacts/{id}?confirm=true` | — | Sin `confirm`, devuelve el contenido del manifiesto y los hashes; con `confirm=true`, descarga y escribe `artifact.downloaded` |

Exportación DXF (§13.1): ezdxf, `R2013` (AC1027), `doc.units = M`, `$INSUNITS = 6`, `$MEASUREMENT = 1`, espacio modelo 1:1 en metros con Y hacia arriba; capas del catálogo y nada creado ad hoc; `line → LINE`, `polyline → LWPOLYLINE`, `point → POINT` (más `INSERT` del marcador si es indicio), `text → TEXT` con estilo `S2C_TEXT` (altura = `height_mm_paper × paper_scale / 1000`), `dimension → DIMENSION` lineal con `render()` y texto sobreescrito por el valor declarado, `symbol → INSERT` del bloque `S2C_<symbol_id>` con `ATTRIB`; metadatos (`plan_id`, `revision_number`, `snapshot_sha256`, `metric_status`, `generator_version`) en XDATA; borrador con el texto `DRAFT — NOT VALIDATED` en `L_SHEET`. Reproducibilidad según el resultado de D-3.

Exportación PDF (§13.2): ReportLab con `invariant=1`, A4 o A3 horizontal, escala entre 1:50, 1:100, 1:200, 1:250, 1:500 (propone la mayor en que el plano cabe con margen de 15 mm; nunca "ajustar a página"); marco, cajetín (`case_file.code`, título, tipo de hecho, fecha, técnico, revisor, revisión, `metric_status`, `snapshot_sha256` abreviado), escala numérica y gráfica, flecha de Norte, leyenda de símbolos usados, tabla de indicios (número, etiqueta, X, Y, origen métrico) y anexo con la imagen original en página aparte con su hash. Borrador: marca de agua diagonal `BORRADOR / DRAFT` y sin tabla de hashes. Leyenda "Borrador técnico, no apto como prueba hasta contar con revisión jurídica" en el PDF final y en el reporte de auditoría, configurable (`legal.disclaimer_enabled = true`).

Manifiesto (§14.2): `manifest_version = "2.0"`, caso, plano, revisión con `snapshot_sha256`, `artifacts[]` con `kind`, `filename`, `sha256`, `events_chain` con `count` y `last_hash`, calibración, sistema local, `metric_status`, `software` (`version`, `git_commit`, `pipeline_version`, `ocr_model`), `catalog_version`, `validated_by`, `validated_at`, `exported_by`, `exported_at`, `timezone_display`, `signature` con `scheme = "ed25519-jcs-v1"`, `public_key_id` y `value` en base64. La firma se calcula sobre los bytes JCS del manifiesto sin el campo `signature`. El ZIP contiene `manifest.json`, los artefactos, el original, `VERIFY.md` y la clave pública. El reporte de auditoría (PDF) incluye el manifiesto legible, la lista de eventos del plano y la declaración de que la firma prueba integridad y origen técnico, no autoría pericial.

Verificador `sketch2cad-verify <zip>`: recalcula SHA-256 de cada artefacto, reconstruye la cadena de eventos y verifica la firma; sale con código 0 si todo coincide y con código 1 nombrando el artefacto o evento que falla.

Clave de firma: archivo en `SIGNING_KEY_PATH` con permisos 600, generada con `python -m sketch2cad.cli generate-signing-key`; nunca en el repositorio. La rotación queda documentada en `docs/signing-keys.md`.

#### 5. System Design (H4)
Jobs `export_dxf`, `export_pdf`, `build_package` encadenados; el estado `EXPORTED` se fija solo cuando `build_package` termina. Las exportaciones borrador usan la revisión de trabajo y no cambian el estado. Módulos `export/dxf.py`, `export/pdf.py`, `export/package.py`, `audit/verify.py`. Pantallas: exportación y descarga (prototipo 6) con tabla de artefactos y hashes antes de confirmar la descarga.

#### 9. Deployment Stages (H4)
Primero DXF y verificación manual en FreeCAD; después PDF; después paquete y verificador. Rollback con `downgrade`; los artefactos ya generados son inmutables. Riesgo: ezdxf no reproducible byte a byte; mitigación: aplicar los ajustes del spike 2 o exigir reproducibilidad sobre el contenido canónico.

#### 10. Testing Strategy y Acceptance Criteria (H4)

| ID | Criterio medible | Prueba |
|---|---|---|
| H4-1 (CA-06) | `doc.audit()` del DXF exportado devuelve 0 errores; releído con ezdxf, el número de entidades por capa coincide con el snapshot y las coordenadas difieren menos de 1e-6 m. | Golden DXF por fixture y relectura. |
| H4-2 | El DXF abre sin errores en FreeCAD (verificación manual por hito, con captura anexa) y en LibreCAD y QCAD en CI con `xvfb` donde sea viable. | Verificación manual documentada en `docs/dxf-viewers.md` y job de CI. |
| H4-3 (CA-07) | Las tres distancias de control (horizontal, vertical, diagonal) coinciden en modelo, DXF y PDF dentro de 0,5 %; una línea de 1 m a 1:100 mide 10 mm ± 0,05 mm (28,35 pt) en el PDF. | Extracción de vectores con pypdfium2 o pdfplumber y de entidades con ezdxf. |
| H4-4 (CA-08) | Dos exportaciones de la misma revisión producen el mismo SHA-256 en PDF; en DXF el mismo, o el mismo contenido canónico si D-3 se resolvió así. | Test de doble generación en procesos separados con 2 s de diferencia. |
| H4-5 (CA-09) | Alterar un byte de cualquier artefacto, del manifiesto o de un evento hace que `sketch2cad-verify` salga con código 1 y nombre el artefacto o evento. | Test que genera un paquete, lo altera en 10 posiciones y verifica. |
| H4-6 | `POST /exports` con `mode = final` sobre un plano en `EDITING` devuelve 409 con el mensaje de V-11; sin permiso `deliver` devuelve 403. | Integración. |
| H4-7 | El borrador lleva la marca `DRAFT` en DXF y PDF y no incluye tabla de hashes; el final incluye la leyenda de borrador técnico. | Test sobre el texto extraído. |
| H4-8 | `GET /artifacts/{id}` sin `confirm=true` no registra `artifact.downloaded`; con `confirm=true` sí. | Integración. |
| H4-9 | Los artefactos finales indican versión de código, pipeline, modelo y catálogo en el manifiesto. | Test sobre el JSON. |
| H4-10 | E2E: validar un plano de ejemplo, exportar el paquete final, descargar el ZIP y verificarlo con `sketch2cad-verify` en el mismo test. | Playwright más ejecución del verificador. |
| H4-11 | La clave de firma no aparece en el repositorio ni en los logs; el compose falla al arrancar si `SIGNING_KEY_PATH` no existe o tiene permisos mayores a 600. | Integración y `gitleaks`. |

---

### Milestone 6 of 7 — H5: Asistencia de IA

**Objetivo.** Tras aceptar la calibración, el técnico recibe polilíneas sugeridas y puede marcar una cota manuscrita para que el OCR proponga el número; todo se acepta, corrige o rechaza por lote. Cubre REQ-17, REQ-18, REQ-19 y CA-04, CA-05. Depende de H2. Primer hito que se recorta si el tiempo se acorta.

#### 3. Database Model Changes (H5)
Sin tablas nuevas. `job_meta` guarda `model_version` y `params`. `entity.source.method` toma `cv` y `ocr`; `entity.confidence` es un número en [0, 1]; `review_status` toma `suggested`, `accepted`, `corrected`, `rejected`. Tabla `config_override` (`scope` global o `incident_type`, `key`, `value`) para REQ-19.

#### 4. API Specifications (H5)

| Endpoint | Request | Response |
|---|---|---|
| `POST /plans/{id}/jobs` con `kind = "suggest_lines"` | `{ "params": { ... } }` opcional | 202 `job`; se encola solo al aceptar la calibración o elegir `non_metric`; relanzable mientras no haya entidades aceptadas |
| `POST /plans/{id}/jobs` con `kind = "ocr_crop"` | `{ "params": { "source_id", "box_px": [x1,y1,x2,y2] } }` | 202 `job`; resultado `{ "text", "value_m", "confidence" }` o `{ "value_m": null }` |
| `GET /jobs/{id}` | — | 200 `{ "status": "queued"|"running"|"succeeded"|"failed", "error"?: { "cause", "action" }, "result_ref"? }` |
| `POST /revisions/{id}/entities:bulk-review` | `{ "ids": [...], "review_status": "accepted"|"rejected" }` | 200 con conteos; evento `entity.reviewed` por entidad |

Pipeline `suggest_lines` (§10): gris → `cv2.adaptiveThreshold` (Gaussian, bloque 31, C 10) → apertura morfológica 3×3 → `skeletonize` → grafo con `sknw` → caminos entre nodos → `shapely.simplify` con tolerancia 0,8 mm de papel → descartar tramos de menos de 5 mm → unir colineales con ángulo menor que 5°. Salida: polilíneas con `review_status = suggested`, `source.method = cv`, `image_region.polygon_px`, `coords_m` provisionales y `metric_origin = trace_only`; `confidence = f(longitud, rectitud)`; resaltadas cuando `confidence < suggestions.min_confidence_highlight` (0,7). Se insertan en la revisión `draft` en una sola transacción con el evento `suggestions.added` y su conteo. `ocr_crop`: TrOCR `microsoft/trocr-base-handwritten` en CPU, normaliza coma a punto, quita "m" y "mts", valida número; confianza mínima para preseleccionar 0,6. El modelo se descarga durante el build de la imagen, no en tiempo de ejecución.

Configuración (REQ-19): todos los parámetros de QC, `suggest_lines`, tolerancias de §11.4 y el umbral de OCR se leen de configuración con sobreescritura por `incident_type`.

#### 5. System Design (H5)
Worker con concurrencia 1 por defecto (CPU). Frontend: capa de sugerencias en color distinto, panel de lote (aceptar todas, rechazar todas, una por una), resaltado por confianza, herramienta "Recorte para OCR" que dibuja una caja sobre la fuente y asocia el valor aceptado a una cota. Fallos: imagen ilegible o job fallido producen estado `failed` con `cause` y `action` visibles (REQ-15).

#### 9. Deployment Stages (H5)
La imagen del worker crece por el modelo; se construye aparte (`Dockerfile.worker`). Rollback: desactivar `suggest_lines` y `ocr_crop` con `ai_assist.enabled = false` en la configuración local (no es un flag de Split) sin tocar la base.

#### 10. Testing Strategy y Acceptance Criteria (H5)

| ID | Criterio medible | Prueba |
|---|---|---|
| H5-1 (CA-04) | Sobre el conjunto sintético, al menos 90 % de los segmentos de más de 20 mm de papel se recuperan con desviación de vértices menor que 2 mm de papel, y hay 0 polilíneas espurias de menos de 5 mm tras el filtro. | Generador sintético (líneas con jitter gaussiano, grosor variable, ruido de papel) y comparación con shapely sobre al menos 20 imágenes. |
| H5-2 | Procesar una imagen de 4000 × 3000 px con `suggest_lines` tarda menos de 30 s en CPU de 4 núcleos; `ocr_crop` devuelve en menos de 3 s. | Test de rendimiento en CI con límites; el hardware queda registrado. |
| H5-3 (CA-05) | Una sugerencia puede aceptarse, corregirse, rechazarse o reemplazarse; después de cada acción, `source`, `confidence` y el historial de eventos se conservan. | Integración sobre entidades y eventos. |
| H5-4 | `bulk-review` con 100 ids cambia el estado de las 100 en una sola transacción y crea 100 eventos; si un id es de otra revisión, no cambia ninguna. | Integración. |
| H5-5 | El OCR normaliza "3,50", "3.50 m" y "3,50mts" a 3,5; un recorte no numérico devuelve `value_m = null`. | Unitario de normalización con 10 casos. |
| H5-6 | Un job fallido muestra en la interfaz una causa y una acción; el plano sigue editable. | Integración y E2E. |
| H5-7 | Con `ai_assist.enabled = false`, los endpoints de jobs de IA devuelven 404 y el editor no muestra los controles. | Integración y E2E. |
| H5-9 | `calibration/accept` ejecutado dos veces sobre el mismo plano crea un solo job `suggest_lines`; crear el plano no crea ninguno. | Integración sobre la tabla de jobs. |
| H5-10 | En `scaled_drawing` con calibración métrica aceptada, una sugerencia nace `trace_only`, sigue `trace_only` mientras está `suggested` y pasa a `scaled_drawing` solo al aceptarla una persona; en `schematic_sketch` sigue `trace_only` tras aceptarla. | Integración sobre `entity.metric_origin` y `compute_metric_status`. |
| H5-8 | Ninguna entidad con `source.method` distinto de `manual` queda en `accepted` sin evento `entity.reviewed`. | Test de invariante sobre la base. |

---

### Milestone 7 of 7 — H6: Endurecimiento y validación interna

**Objetivo.** El conjunto completo de criterios CA-01 a CA-13 pasa en CI y un informe mide G-1 a G-3 sobre croquis propios, declarando que la medición es interna. Cubre REQ-19 (cierre), REQ-20, CA-11, CA-12 completo y CA-13. Depende de H4; depende de H5 solo para CA-04, CA-05 y G-3, que se verifican si H5 se construyó.

#### 3. Database Model Changes (H6)
Sin cambios de esquema previstos. Solo migraciones de corrección si las pruebas encuentran defectos. Tabla `timing_log` opcional (`plan_id`, `phase`, `started_at`, `ended_at`) si el cronometraje se hace dentro de la aplicación; en caso contrario se usa una hoja de registro en `docs/validation/timings.csv`.

#### 4. API Specifications (H6)
Sin endpoints nuevos. Se completa el manejo de errores explícito (CA-11): imagen ilegible, calibración fuera de tolerancia y job fallido devuelven problema RFC 9457 con `cause` y `action`.

#### 5. System Design (H6)
Conjunto de validación en `validation/`: al menos 10 croquis dibujados a mano (croquis esquemáticos con cotas, triangulaciones desde dos esquinas, línea base más offset en una vía), un patrón impreso fotografiado para CA-02 y un conjunto sintético generado por script con geometría conocida. Todos sin datos reales de expedientes. Informe `docs/validation/report.md` con: método de cronometraje, tiempo en Sketch2CAD y en FreeCAD por croquis (G-1, línea base sin umbral), coincidencia de cotas declaradas con las exportadas revisada por una segunda persona (G-2, objetivo ≥ 99 %), tasa de polilíneas aceptadas sin cambios y tiempo con y sin sugerencias (G-3, línea base sin umbral), y la declaración de que la medición es interna y de que la validación con técnicos reales queda pendiente.

#### 9. Deployment Stages (H6)
Congelar versiones, ejecutar la prueba de restauración (`pg_dump` más objetos) y generar el informe. Rollback: no aplica; es un hito de verificación.

#### 10. Testing Strategy y Acceptance Criteria (H6)

| ID | Criterio medible | Prueba |
|---|---|---|
| H6-1 | Los criterios CA-01 a CA-03 y CA-06 a CA-13 pasan en CI en una misma ejecución; CA-04 y CA-05 también si H5 se construyó, y si no, el informe los declara como validación posterior. | Workflow de CI con una etiqueta por criterio. |
| H6-2 (CA-11) | Una imagen con varianza del Laplaciano < 40, una calibración con error > 1,0 mm y un job forzado a fallar muestran cada uno un estado con `cause` y `action` no vacíos. | Integración por camino de error. |
| H6-3 (CA-12) | Con dos usuarios, el no autorizado recibe 403 o 404 en lectura, edición, exportación y descarga, y cada intento deja un evento `access.denied`. | Integración. |
| H6-4 (CA-13) | `pip-licenses` y `license-checker` no reportan ninguna dependencia fuera de la lista blanca ni excepción sin documentar. | Job de CI. |
| H6-5 | Existen ≥ 10 croquis dibujados a mano, el patrón fotografiado y el conjunto sintético, todos en `validation/`. | Conteo en el repositorio. |
| H6-6 | El informe contiene G-1, G-2 y G-3 con las cifras medidas y la declaración de alcance interno. | Revisión del documento. |
| H6-7 | La restauración desde `pg_dump` más el directorio de objetos reproduce los mismos `sha256` de los originales y de los snapshots validados. | Script `scripts/restore_check.sh`. |
| H6-8 | Ningún archivo de log de la ejecución completa de las pruebas contiene coordenadas ni nombres de archivo de croquis. | Búsqueda automática sobre los logs de CI. |

---

## 9. Deployment Stages (global)

1. H0: repositorios, compose y CI; migración `0001`.
2. H1: migración `0002`; volumen `objects`; copia de respaldo documentada.
3. H2: migración `0003`; catálogo semilla.
4. H3: migración `0004`.
5. H4: sin migración de esquema salvo índices; clave de firma generada en el host.
6. H5: imagen del worker con modelo; interruptor de configuración `ai_assist.enabled`.
7. H6: congelar, restaurar, informar.

Despliegue: un solo host con cifrado de disco, TLS terminado en nginx, `docker compose up -d`. Rollback general: `git revert` más `alembic downgrade` al número anterior; los objetos del almacenamiento no se borran. Entre etapas se hace un respaldo.

Riesgos y mitigaciones:

| Riesgo | Mitigación |
|---|---|
| Una sola persona: un retraso mueve todo el plan | H5 se recorta; revisión de plan al cierre de cada hito |
| Contrato API-web desincronizado | Cliente generado desde `openapi.json` en el mismo repositorio y CI que falla si difiere |
| El editor SVG no alcanza el rendimiento | Medición en H0; Konva antes de H2 si falla |
| DXF no reproducible byte a byte | Spike 2 en H0; alternativa de reproducibilidad por contenido canónico |
| Calidad de las sugerencias sobre croquis reales desconocida | Sugerencias opcionales; medición declarada interna en H6 |
| Uso probatorio indebido antes de revisión jurídica | Leyenda de borrador técnico por defecto; firma descrita como integridad y origen técnico |
| Dependencias con licencia fuera de la lista | CI con lista blanca y excepciones documentadas |

## 10. Testing Strategy (global)

- **Unitario:** funciones geométricas, QC, normalización de OCR, encadenamiento de hashes, serialización canónica (pytest, hypothesis para propiedades).
- **Integración:** API contra PostgreSQL real y almacenamiento en directorio temporal; máquina de estados; permisos con dos usuarios.
- **E2E:** Playwright contra el compose completo (api, worker, db, web), nunca contra un servidor estático, porque login, ingesta y exportación necesitan el backend. La tarea de CI que levanta el stack es parte de H0.
- **Contrato:** CI regenera el cliente TypeScript desde `openapi.json` y falla si el resultado difiere del versionado.
- **Datos de prueba:** solo croquis sintéticos y croquis dibujados a propósito; ningún dato de expedientes reales; ningún log con imágenes ni geometría.
- **Seguridad:** `pip-audit`, `pnpm audit --audit-level=high`, `gitleaks` y chequeo de licencias en cada ejecución de CI.
- **Medibles:** todos los criterios llevan número o comportamiento observable; los umbrales de rendimiento de H0 (60 fps, p95 ≤ 25 ms, login < 300 ms, arranque < 90 s) fueron confirmados por el dueño del proyecto el 2026-10-24.
- **Criterios vacuos descartados:** no se incluye un criterio de "cookie ausente" ni de "sin secretos" sin una prueba que pueda fallar; ambos se verifican con una rama o fixture que los viola a propósito (H0-7, H0-8, H0-9).

## 11. Frontend Components

Sin Data UI Inventory ni eventos GTM: el dueño no los solicitó. La referencia visual es el prototipo de seis pantallas (https://claude.ai/artifact/QQiw5ef4cdATbbfWV3MbNi): lista de expedientes, carga y control de calidad, calibración, editor 2D, revisión, exportación. El diseño visual definitivo del editor se ajusta en H2 con el resultado del spike 1. Integración con el backend: cliente TypeScript generado desde el esquema OpenAPI de la sección 4 de cada hito.
