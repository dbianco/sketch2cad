# Sketch2CAD

Aplicación web que convierte el croquis de campo de un siniestro vial, una escena del crimen o una inspección ocular en un plano 2D editable, medible y auditable, y lo exporta a DXF y PDF técnico. La geometría final se reconstruye a partir de las cotas del técnico, no de los píxeles; la IA solo propone y el perito valida.

Este repositorio contiene la documentación y las especificaciones. El código vive en dos repositorios aparte (`sketch2cad-api` y `sketch2cad-web`), que se crean en el hito H0.

## Estructura

| Ruta | Contenido |
|---|---|
| `docs/specs/Sketch2CAD_MVP.md` | Especificación vigente del MVP (v2.0) |
| `docs/specs/archive/` | Especificaciones anteriores, solo como antecedente |
| `docs/evaluation/` | Evaluación de las especificaciones originales |
| `docs/prototype/` | Prototipo de seis pantallas (referencia visual) |
| `docs/references/` | Material de referencia (el PDF del artículo no se versiona) |
| `.sdlc/features/001-sketch2cad-mvp/` | PRD, scoping y jot down de la feature (`prd.md`, `scoping-doc.md`, `jotdown.md`). Esta carpeta debe estar en la raíz porque el plugin SDLC la busca ahí. |

## Estado

Especificación y PRD completos; scoping y jot down en borrador; el hito H0 (fundación) es el siguiente.
