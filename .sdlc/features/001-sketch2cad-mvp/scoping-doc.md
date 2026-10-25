# Scoping — Sketch2CAD MVP

**PRD Reference:** [prd.md](./prd.md) (Draft v0.2)
**Fuente técnica:** `docs/specs/Sketch2CAD_MVP.md` v2.0
**Author:** Daniel Oliva
**Date:** 2026-10-24
**Status:** Draft

---

## 1. Context

Sketch2CAD convierte el croquis de campo de un siniestro vial, una escena del crimen o una inspección ocular en un plano 2D editable, medible y auditable, y lo exporta a DXF y PDF técnico (PRD, Problem Statement, sección 1). La geometría final se reconstruye a partir de las cotas del técnico, no de los píxeles. La IA solo propone y el perito valida.

El caso de negocio (PRD sección 2) es interno: sin ingresos, con ahorro de horas-persona. La estimación de partida es 1,0 h por croquis; el equilibrio de la inversión (600 h) se recalcula con la medición de H6.

Goals que cubre este documento: G-1 a G-7.

## 2. Problem

Hoy el paso de croquis a plano CAD es manual, sin trazabilidad entre lo que dibujó el técnico, lo que corrigió y lo que validó el perito. Las herramientas por calibración de píxeles dan distancias falsas con apariencia de precisión. En términos técnicos, el sistema debe:

- Guardar el original de forma inmutable y verificable por hash.
- Mantener un modelo canónico de plano donde la cota declarada manda sobre la medida derivada.
- Registrar cada decisión en un log encadenado por hashes.
- Exportar DXF y PDF reproducibles con escala verificable, y un paquete final firmado.

## 3. Solution

Aplicación web con tres procesos: API (FastAPI), worker (Procrastinate sobre PostgreSQL, sin broker) y editor web (React + SVG). Almacenamiento de archivos por contenido detrás de una interfaz `Storage`. Detalle completo en `docs/specs/Sketch2CAD_MVP.md`, secciones 7 a 15.

### 3.1 Milestones

Cada hito es un incremento utilizable de extremo a extremo.

| # | Milestone | Description | REQ Coverage | Dependencies |
|---|---|---|---|---|
| H0 | Fundación operativa | Entrar con usuario, crear un expediente vacío y ver CI verde con chequeo de licencias. Incluye medición de 60 fps del editor SVG y el spike de reproducibilidad del DXF. | REQ-16, REQ-01 (login) | Ninguna |
| H1 | Expedientes e ingesta | Crear expedientes con permisos, subir un croquis, ver su hash y el semáforo de calidad, y consultar el log de eventos encadenado. | REQ-01, REQ-02, REQ-11 | H0 |
| H2 | Calibración y editor | Calibrar la fuente, fijar el sistema local, dibujar y editar un plano con símbolos, deshacer y rehacer, y guardar revisiones. | REQ-03, REQ-04, REQ-05, REQ-09, REQ-10 | H1 |
| H3 | Herramientas métricas | Reconstruir el plano a partir de cotas: fijar longitud, triangulación, base más offset, tabla de indicios y estado métrico. | REQ-06, REQ-07, REQ-08 | H2 |
| H4 | Exportación y auditoría | Validar un plano, exportar DXF y PDF (borrador y final), y descargar un paquete firmado que un verificador independiente comprueba. | REQ-12, REQ-13, REQ-14, REQ-15 | H3; spike 2 de H0 |
| H5 | Asistencia de IA | Recibir polilíneas sugeridas y un OCR asistido de cotas, aceptar o rechazar por lote. Primer hito que se recorta si el tiempo se acorta. | REQ-17, REQ-18 | H2 |
| H6 | Endurecimiento y validación interna | Pasar CA-01 a CA-13, medir G-1 a G-3 con ≥ 10 croquis propios y emitir el informe. | REQ-19, REQ-20 | H4 (H5 para CA-04, CA-05 y G-3) |

Nota: los REQ-21 a REQ-24 (P2) quedan fuera de este scoping.

## 4. Scope

### In Scope

- REQ-01 a REQ-20 (P0 y P1) en los hitos H0 a H6.
- Prototipo HTML de seis pantallas como referencia de diseño (PRD sección 6).
- Despliegue con docker-compose en un host con cifrado de disco.

### Out of Scope

- REQ-21 a REQ-24: detector de símbolos, control múltiple de calibración, operación institucional, modo offline.
- Las exclusiones NG-1 a NG-12 del PRD.
- Georreferenciación, servicios externos de IA, AutoCAD como visor verificado.

## 5. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Una sola persona: cualquier retraso mueve todo el plan (G-6). | H5 es recortable; revisión de plan al cierre de cada hito; medición de 60 fps y spike de DXF en H0. |
| El contrato API–web puede desincronizarse. | Generar el cliente TypeScript desde el esquema OpenAPI de FastAPI en el mismo repositorio y fallar el CI si cambia sin regenerarse. |
| Un solo repositorio dispara ambos CI en cada cambio. | Filtros `paths:` por carpeta en cada workflow. |
| El editor SVG no alcanza 60 fps con 2000 entidades. | Medición en H0; si falla, migrar a Konva (REQ-05) antes de H2. |
| ezdxf no produce DXF idénticos byte a byte. | Spike 2 en H0 documenta cómo fijar handles, `$TDCREATE` y GUIDs; si no es posible, la reproducibilidad se exige sobre el contenido canónico, no sobre los bytes. |
| Calidad de las sugerencias de IA sobre croquis reales es desconocida. | Las sugerencias son opcionales; validación solo con croquis propios y sintéticos; declarado en el informe de H6. |
| Dependencias LGPL y excepciones a la norma de licencias de la empresa. | Excepción documentada por cada LGPL; CI falla ante una dependencia fuera de la lista (G-7). |
| Uso probatorio indebido antes de revisión jurídica (DEC-8). | Leyenda "Borrador técnico, no apto como prueba" activa por defecto en reporte y PDF (REQ-14). |
| Sin técnicos reales, las métricas son internas. | Se declaran como tales en el informe; la observación con técnicos queda fuera del MVP. |

## 6. Architectural Decisions

| Decision | Options Considered | Chosen Approach | Rationale |
|---|---|---|---|
| Organización del código | Monorepo; dos repositorios; pendiente | Un solo repositorio con dos carpetas: `sketch2cad-api/` (API, worker, migraciones, docker-compose) y `sketch2cad-web/` (editor) | Decisión del dueño (2026-10-25), que reemplaza la de dos repositorios. Un solo historial y un solo CI con filtros por ruta. |
| Tecnología del editor 2D | SVG; Konva; pendiente de spike | SVG | Decisión del dueño. Accesible por teclado y editable. REQ-05 mantiene el criterio de 60 fps con migración a Konva si falla. |
| Reproducibilidad del DXF | Byte a byte; solo contenido canónico | ⚠️ PENDING DECISION | Depende del spike 2 de H0. Por defecto se intenta byte a byte. |
| Backend | Java + Python; Python único | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic | ezdxf, OpenCV, shapely y PDF son Python; un solo lenguaje para un desarrollador. |
| Procesamiento asíncrono | Broker de mensajes; jobs en PostgreSQL | Procrastinate sobre PostgreSQL 16 | Sin infraestructura adicional; reintentos idempotentes. |
| Almacenamiento | MinIO; S3; disco | Disco direccionado por contenido detrás de `Storage` | MinIO es AGPL; S3 con Object Lock queda como driver posterior. |
| Exportación | DXF rasterizado; modelo canónico a DXF y PDF | ezdxf (R2013, metros) y ReportLab con `invariant=1` | Escala exacta y PDF reproducible. |
| Autenticación | OIDC; local | Usuarios locales con Argon2id | OIDC y MFA son fase 3. |
| Verificación de DXF | Solo AutoCAD; FreeCAD; LibreCAD y QCAD | FreeCAD (manual, disponible localmente) más LibreCAD y QCAD en CI donde sea viable | DEC-7. |
| Hosting | Nube gestionada; docker-compose | docker-compose en un host con cifrado de disco; clave de firma en archivo con permisos 600 | DEC-9. |
| Integridad | Firma institucional; Ed25519 de servidor | Manifiesto JSON canónico (RFC 8785) y firma Ed25519 de servidor | DEC-8; se declara integridad y origen técnico, no autoría pericial. |

## 7. Affected Services

| Service | Changes Required | Estimated Complexity (S/M/L) |
|---|---|---|
| `sketch2cad-api` (FastAPI) | Usuarios, expedientes, ingesta, calibración, plano canónico, revisiones, máquina de estados, eventos encadenados, exportación y manifiesto. REQ-01 a REQ-04, REQ-06 a REQ-16 | L |
| `sketch2cad-api` worker (Procrastinate) | Control de calidad, `suggest_lines`, OCR asistido, exportaciones pesadas. REQ-02, REQ-12, REQ-13, REQ-17, REQ-18 | M |
| PostgreSQL 16 | Esquema, migraciones con vuelta atrás probada, cola de jobs. REQ-10, REQ-11, REQ-16 | M |
| `sketch2cad-web` (React, TypeScript, Vite) | Pantallas del prototipo, editor SVG, herramientas métricas, revisión y descarga. REQ-03 a REQ-08, REQ-10, REQ-14, REQ-15 | L |
| Verificador independiente | Herramienta que comprueba hashes, cadena de eventos y firma sin servidor. REQ-14 | S |
| CI (GitHub Actions) | Tests, chequeo de licencias, auditoría de DXF, relectura, migraciones, hallazgos de seguridad. REQ-16 | M |

## 8. Feature Flag (Split.io)

Omitida: el dueño del proyecto indicó que no se requiere feature flag. Es un producto nuevo y sin usuarios actuales.
