# Sketch2CAD

Aplicación web que convierte el croquis de campo de un siniestro vial, una escena del crimen o una inspección ocular en un plano 2D editable, medible y auditable, y lo exporta a DXF y PDF técnico.

> **Estado:** fase de especificación. Este repositorio contiene la documentación; todavía no hay código. El producto es un borrador técnico y no está pensado como prueba hasta contar con revisión jurídica.

## Idea central

Un croquis hecho a mano normalmente no está a escala. La información métrica real está en las **cotas escritas** y en mediciones de referencia (triangulación desde dos puntos fijos, línea base y offset), no en los píxeles. Por eso Sketch2CAD reconstruye la geometría a partir de las cotas que declara el técnico:

- La **cota declarada manda** sobre la medida derivada; el sistema nunca corrige una por su cuenta.
- La **IA solo propone** (polilíneas sugeridas y OCR asistido de cotas); una persona acepta, corrige o rechaza.
- Cada plano tiene un **estado métrico** (`metric`, `approximate`, `non_metric`) que se imprime en el DXF y el PDF.
- Cada acción queda en un **log de eventos encadenado por hashes**, y el paquete final lleva un manifiesto firmado que un auditor externo puede verificar sin acceso al servidor.

## Alcance del MVP

Incluye: expedientes y fuentes inmutables con hash, calibración imagen a papel, sistema de coordenadas local, editor 2D en el navegador, herramientas métricas (fijar longitud, triangulación, base más offset), revisión y validación con congelado de revisiones, exportación a DXF y PDF a escala, paquete final firmado y verificador independiente. Las sugerencias de IA son opcionales.

No incluye: 3D, georreferenciación, detección automática de evidencias, pericias autónomas, servicios externos de IA, MFA ni firma institucional.

## Plan

Una persona a tiempo completo, 15 semanas, siete hitos secuenciales.

| Hito | Contenido | Semanas |
|---|---|---|
| H0 | Fundación: repositorios, docker-compose, auth local, CI y licencias, dos comprobaciones técnicas | 1 |
| H1 | Expedientes, ingesta con hash y control de calidad, eventos encadenados | 2 |
| H2 | Calibración, sistema local, editor SVG, revisiones | 3 |
| H3 | Herramientas métricas, tabla de indicios, estado métrico | 2 |
| H4 | Exportación DXF y PDF, manifiesto firmado, verificador | 3 |
| H5 | Asistencia de IA (recortable) | 2 |
| H6 | Endurecimiento y validación interna con 10 o más croquis propios | 2 |

## Stack previsto

Python 3.12 con FastAPI, SQLAlchemy y Alembic; worker Procrastinate sobre PostgreSQL 16; React 18 con TypeScript y Vite; editor en SVG; OpenCV, scikit-image y shapely; ezdxf para DXF y ReportLab para PDF; firma Ed25519 con JSON canónico (RFC 8785). Todo se ejecuta con `docker compose`. El código vivirá en dos repositorios: `sketch2cad-api` y `sketch2cad-web`.

## Estructura

| Ruta | Contenido |
|---|---|
| `docs/specs/Sketch2CAD_MVP.md` | Especificación vigente del MVP (v2.0), fuente normativa |
| `docs/specs/archive/` | Especificaciones anteriores, solo como antecedente |
| `docs/evaluation/` | Evaluación de las especificaciones originales |
| `docs/prototype/` | Prototipo de seis pantallas (referencia visual) |
| `docs/references/` | Material de referencia (el PDF del artículo no se versiona) |
| `.sdlc/features/001-sketch2cad-mvp/` | `prd.md`, `scoping-doc.md` y `jotdown.md` de la feature. Debe estar en la raíz porque el plugin SDLC la busca ahí. |

## Por dónde empezar

1. Lee `docs/specs/Sketch2CAD_MVP.md` para el producto completo.
2. Lee `.sdlc/features/001-sketch2cad-mvp/prd.md` para objetivos, requisitos y decisiones.
3. Lee `.sdlc/features/001-sketch2cad-mvp/jotdown.md` para el detalle de implementación por hito.

## Licencia

Sin licencia por ahora. Mientras no se agregue una, todos los derechos quedan reservados.
