# Sketch2CAD — Especificación del MVP

**Versión:** 2.0
**Fecha:** 2026-10-24
**Estado:** Lista para planificar la construcción del MVP.
**Reemplaza a:** `docs/specs/archive/Sketch2CAD_v1.md` v1.0 y a las dos especificaciones originales `docs/specs/archive/forensicsketch_to_cad_parte_*.md`, que quedan como antecedentes.
**Fundamento de los cambios:** `docs/evaluation/Evaluacion_especificaciones.md`.

Convenciones del documento: el texto está en español; los identificadores de código, tablas, campos, capas, estados y endpoints están en inglés porque así se escribirá el software. Las decisiones que tomó el autor de esta spec sin consulta previa están marcadas como **[decisión del autor]**; cualquiera puede revertirse antes de empezar a construir.

---

## 1. Propósito e hipótesis de valor

Sketch2CAD transforma un croquis de campo (siniestro vial, escena del crimen, inspección ocular) en un plano 2D estructurado, medible, editable y exportable a DXF y PDF, con trazabilidad completa de cada decisión.

La IA propone; el técnico decide, corrige y valida. El sistema no reemplaza el levantamiento, la interpretación pericial ni la aprobación del profesional responsable.

**Hipótesis que el MVP debe permitir medir** (no se dan por demostradas):

| Hipótesis | Cómo se mide |
|---|---|
| H1. Pasar un croquis a un plano CAD con Sketch2CAD tarda menos que hacerlo a mano en CAD. | Tiempo desde la carga hasta la exportación final, comparado con el tiempo de dibujar el mismo plano en un CAD libre (FreeCAD), en al menos 10 croquis. En v1 se mide y se reporta la línea base (estimación de partida: 1 h por croquis) sin umbral; el umbral se fija para v2. En el MVP la medición es interna (ver §18); la comparación con el método real de los técnicos queda pendiente. |
| H2. Las cotas del plano final coinciden con las del croquis (menos errores de transcripción). | Porcentaje de cotas declaradas cuyo valor exportado coincide con el escrito en el croquis, verificado por una segunda persona. |
| H3. Las sugerencias de IA ahorran trabajo en vez de añadirlo. | Tasa de aceptación de polilíneas sugeridas y de valores OCR, y tiempo de edición con y sin sugerencias sobre el mismo croquis. |

Mientras no haya técnicos disponibles, estas tres medidas se toman internamente y se reportan como tales. No se afirma nada sobre la práctica real hasta que haya observación con usuarios.

## 2. Alcance del MVP

### 2.1 Incluido

1. Expedientes con tipo de hecho, estado, usuarios autorizados.
2. Ingesta de fuentes (JPEG, PNG, TIFF, PDF de una página) con hash SHA-256 y almacenamiento inmutable.
3. Control de calidad de imagen como advertencia.
4. Calibración imagen→papel: escaneo (DPI o segmento de referencia) y fotografía (homografía de 4 puntos). Modo no métrico.
5. Declaración del **modo de fuente**: `scaled_drawing` (plano a escala) o `schematic_sketch` (croquis esquemático).
6. Sugerencia automática de polilíneas por esqueletización del trazo. Aceptación y rechazo por lote.
7. OCR asistido de cotas: el técnico marca un recorte, el sistema propone el valor.
8. Editor 2D en el navegador: pan/zoom, original superpuesto, dibujar línea, polilínea, punto, texto, cota y símbolo; mover vértices; snapping registrado; deshacer/rehacer; medir; fijar longitud a cota.
9. Posicionamiento de puntos por **triangulación** (dos distancias) y por **línea base + offset**.
10. Registro de indicios por expediente (numeración única) y tabla de indicios con coordenadas locales en cada plano.
11. Paleta de símbolos desde un catálogo configurable (sin detección automática).
12. Revisiones inmutables, log de eventos encadenado, validación por revisor, reapertura con nueva revisión.
13. Exportación **borrador** (marca de agua) y exportación **final** (solo desde revisión validada): DXF R2013, PDF técnico a escala normalizada con anexo del original, manifiesto firmado, reporte de auditoría en PDF y paquete ZIP.
14. Cola de trabajos sobre PostgreSQL con un worker y polling de estado.
15. Autenticación local con roles y permisos por expediente.

### 2.2 Excluido del producto (no objetivos)

- Reconstrucción 3D; nubes de puntos, drones, LiDAR.
- Identificación automática de cadáveres, sangre, armas, casquillos o impactos.
- Trayectorias balísticas o conclusiones sobre la mecánica del hecho.
- Georreferenciación o integración con GIS.
- Generación autónoma de una pericia con validez judicial.
- Entrenamiento de modelos con datos forenses sin autorización y gobierno de datos.

### 2.3 Diferido a fases posteriores (con su punto de extensión en el MVP)

| Capacidad | Fase | Punto de extensión que el MVP deja |
|---|---|---|
| Detector de símbolos entrenado | 2 | Las sugerencias ya nacen con `source.method = "model"` y `model_version`; el worker tiene una etapa `detect_symbols` vacía. |
| OCR automático de página completa | 2 | El worker tiene una etapa `detect_text_regions`; el OCR asistido reutiliza el mismo reconocedor. |
| Varios puntos de control con ajuste por mínimos cuadrados | 2 | `Calibration.method` admite nuevos valores; se guardan todos los puntos. |
| Modelo multimodal (p. ej. Claude) para proponer entidades | 2, solo con autorización institucional y flag | `source.method = "model"`; política de procesamiento externo en configuración. |
| OIDC contra IdP institucional, MFA | 3 | Módulo `auth` con interfaz `IdentityProvider`. |
| Legal hold, retención y borrado programado | 3 | Campos `retention_policy_id` y `legal_hold` en `case_file` (sin lógica). |
| Firma institucional (PAdES, RFC 3161) | 3 | `Manifest.signature` con `scheme` versionado. |
| S3 con Object Lock | 3 | Interfaz `Storage` con driver `filesystem` y driver `s3` stub. |
| Modo offline / PWA / táctil optimizado | 3 | Ninguno; se evaluará con evidencia de necesidad. |
| Catálogo institucional con colores y tipos de línea | 3 | El catálogo es versionado y configurable; el MVP incluye uno por defecto. |
| Composición de varias fuentes en un plano | 3 | `plan.primary_source_id`; otras fuentes se adjuntan sin componerse. |

### 2.4 Antiobjetivos

El sistema falla si:

- presenta una inferencia de IA como hecho confirmado;
- altera o reemplaza el original;
- exporta un plano "a escala" cuya geometría no proviene de cotas, triangulación o un dibujo declarado a escala;
- oculta incertidumbre o errores de reconocimiento;
- permite exportar un paquete final sin validación humana;
- hace pasar un hash por cadena de custodia completa.

## 3. Usuarios y roles

| Rol | Puede |
|---|---|
| `technician` | Crear expedientes propios, cargar fuentes, calibrar, editar planos, pedir sugerencias, exportar borradores. |
| `reviewer` | Todo lo anterior más validar/bloquear revisiones, reabrir, exportar paquete final y descargarlo. |
| `admin` | Gestionar usuarios, roles, catálogo, configuración; no edita planos por defecto. |

**[decisión del autor]** El rol "custodio" de la v1.0 se expresa como el permiso `case_file:deliver` que por defecto tiene `reviewer`. Una institución que exija separación de funciones lo reasigna en configuración sin cambiar código.

Los permisos se evalúan por expediente: un usuario solo ve los expedientes en los que está autorizado.

## 4. Conceptos

| Concepto | Definición |
|---|---|
| **Expediente** (`case_file`) | Unidad de trabajo asociada a un hecho. Agrupa fuentes, planos y eventos. |
| **Fuente** (`source`) | Un archivo cargado (foto o escaneo de un croquis). Inmutable. Tiene hash, metadatos, QC y modo de fuente. |
| **Modo de fuente** | `scaled_drawing`: el papel se dibujó a escala declarada; las distancias en papel son métricas. `schematic_sketch`: el dibujo es topológico; la geometría final se reconstruye a partir de cotas. Es el valor por defecto. |
| **Plano** (`plan`) | El modelo 2D que se edita y exporta. Tiene una fuente primaria, una calibración y revisiones. |
| **Revisión** (`revision`) | Snapshot inmutable del plano en JSON canónico, con hash. La edición ocurre en una revisión de trabajo (`draft`); validar la congela. |
| **Entidad** (`entity`) | Elemento del plano: línea, polilínea, punto, texto, cota, símbolo. Tiene geometría en mundo, procedencia y estado de revisión. |
| **Transformación imagen→papel** | Homografía (fotografía) o escala uniforme (escaneo con DPI). Lleva píxeles a milímetros de papel. Sirve para procedencia y para que las sugerencias caigan sobre el trazo. |
| **Transformación papel→mundo** | Solo existe en `scaled_drawing`: escala declarada (1:100, 1:200...). En `schematic_sketch` no existe; las coordenadas mundo salen de cotas. |
| **Cota declarada** | Valor numérico escrito en el croquis o ingresado por el técnico. Es la verdad métrica. |
| **Medida derivada** | Distancia calculada a partir de la geometría del modelo. Nunca sobrescribe una cota declarada; si difieren, se señala. |
| **Estado métrico del plano** | `metric`: toda la geometría estructural proviene de cotas, triangulación o `scaled_drawing` calibrado. `approximate`: alguna parte proviene solo del trazo. `non_metric`: sin calibración ni cotas. Se imprime en DXF y PDF. |
| **Sistema de coordenadas local** | Origen, eje X e Y definidos por el técnico sobre el plano, unidad metro, Norte como ángulo respecto a +Y. No es georreferencia. |
| **Indicio** | Registro a nivel de expediente (`evidence`) con número único, etiqueta y atributos. Cada plano lo ubica mediante una entidad `symbol` o `point` que referencia `evidence_id`. El indicio nº 7 es el mismo en el plano general y en el de detalle. **[decisión del autor]** |
| **Restricción métrica** (`constraint`) | Definición guardada en una entidad fijada por triangulación o base+offset: a qué referencias apunta y con qué distancias. Si una referencia se mueve, la entidad se recalcula. Dependencia unidireccional; no se admiten ciclos. **[decisión del autor]** |

## 5. Flujos

### 5.1 Flujo principal

1. El técnico crea un expediente (tipo de hecho, fecha, descripción, ubicación declarada).
2. Carga una o más fuentes. Por cada una el sistema calcula SHA-256, guarda el original inmutable, extrae metadatos y ejecuta QC. Las advertencias se muestran pero no bloquean.
3. El técnico crea un plano desde una fuente primaria y declara el modo de fuente.
4. El técnico calibra imagen→papel: escaneo (DPI del archivo o segmento de referencia) o fotografía (4 puntos del contorno del papel u otro rectángulo conocido). Ve el error de reproyección y acepta. O elige `non_metric`.
5. Al aceptar la calibración el sistema genera la imagen rectificada y encola `suggest_lines` sobre ella (sobre el original si `non_metric`). Mientras el worker trabaja, el técnico sigue con los pasos 6 y 7.
6. Si el modo es `scaled_drawing`, declara la escala del dibujo y verifica con al menos una cota conocida.
7. Define el sistema local: origen, dirección +X (p. ej. a lo largo de una pared o cordón), Norte. Puede cambiarlo después: el cambio es una transformación rígida de todo el modelo y queda registrado como un solo evento.
8. El editor muestra la fuente rectificada de fondo y las sugerencias de polilíneas en estado `suggested`. El técnico acepta o rechaza por lote y afina. La calibración queda bloqueada desde que existe la primera entidad aceptada o manual; para recalibrar hay que descartar las entidades o crear otro plano.
9. El técnico dibuja o corrige entidades; asigna cotas declaradas con "fijar longitud"; coloca indicios por triangulación o base+offset; coloca símbolos desde la paleta; para leer una cota manuscrita marca un recorte y acepta o corrige el valor OCR.
10. Puede exportar un **borrador** (DXF y PDF con marca de agua `DRAFT`) en cualquier momento.
11. Cuando termina, pasa el plano a `REVIEW_PENDING`.
12. El revisor abre el plano, revisa, corrige si hace falta (cada cambio queda en el log) y lo pasa a `VALIDATED`. La revisión de trabajo se congela.
13. El revisor genera la **exportación final**: DXF, PDF técnico, reporte de auditoría, manifiesto firmado, ZIP. El plano pasa a `EXPORTED`.
14. El revisor descarga el paquete; la descarga queda registrada.

### 5.2 Flujos alternativos

| Situación | Comportamiento |
|---|---|
| Fuente ilegible (QC en rojo) | Se permite continuar en modo manual con advertencia persistente; `suggest_lines` puede devolver cero entidades sin error. |
| Homografía con error de reproyección sobre el umbral | La calibración no se puede aceptar como `metric`; el sistema ofrece repetir los puntos o continuar como `approximate`. |
| Cota declarada incompatible con otras (p. ej. triángulo imposible) | La herramienta indica que no hay solución y no mueve nada. |
| Trabajo del worker falla o expira | El job queda en `FAILED` con mensaje; el técnico puede reintentar; el original no se duplica. |
| Reapertura de un plano `VALIDATED` o `EXPORTED` | Solo `reviewer`; crea una nueva revisión `draft` a partir de la validada, el plano vuelve a `IN_REVIEW`; la revisión validada y sus artefactos permanecen recuperables. |
| Dos usuarios abren el mismo plano para editar | El primero obtiene un lease de edición (10 min renovable); el segundo entra en solo lectura hasta que el lease expire o se libere. |
| Descarga del paquete final | Muestra el contenido y los hashes antes de confirmar; registra quién, cuándo, desde qué IP. |

## 6. Casos de uso del MVP

Cada caso incluye criterio de aceptación verificable y la prueba que lo cubre.

### CU-01. Expediente y fuentes

- **Actor:** `technician`.
- **Flujo:** crea expediente; carga un archivo; el sistema calcula SHA-256 en servidor, compara con el hash calculado en el cliente, guarda el original, registra metadatos (EXIF/DPI si existen), ejecuta QC.
- **Criterio:** el original descargado posteriormente tiene exactamente el hash registrado; el QC devuelve semáforo y motivos.
- **Prueba:** test de integración que carga un archivo, descarga el original y compara hashes; test unitario de QC con imágenes sintéticas borrosas, pequeñas y correctas.

### CU-02. Calibración imagen→papel y modo de fuente

- **Actor:** `technician`.
- **Flujo A (escaneo):** el sistema propone la escala desde el DPI del archivo; el técnico puede corregir marcando un segmento de longitud conocida en papel (p. ej. 100 mm de una regla dibujada).
- **Flujo B (fotografía):** el técnico marca 4 esquinas del papel o de un rectángulo de dimensiones conocidas; el sistema calcula la homografía, muestra la imagen rectificada y el error de reproyección.
- **Flujo C (sin calibración):** `non_metric`.
- **Criterio:** con una fotografía oblicua de un patrón impreso de dimensiones conocidas, las distancias en papel tras rectificar quedan dentro de la tolerancia configurada (por defecto 1 % o 1 mm, el mayor). Una fotografía no rectificada no puede marcarse `metric`.
- **Prueba:** fixture con fotografía sintética generada por `cv2.warpPerspective` de un patrón conocido; se verifica la inversa.

### CU-03. Sugerencias automáticas

- **Actor:** sistema (worker).
- **Flujo:** `suggest_lines` binariza, esqueletiza, extrae el grafo de trazos, simplifica y devuelve polilíneas en coordenadas de imagen con `confidence` (derivada de longitud y rectitud) y `review_status = "suggested"`. El editor las muestra en color de sugerencia y permite aceptar todas, rechazar todas o una por una.
- **Criterio:** sobre croquis sintéticos con N segmentos conocidos de más de 20 mm de papel, al menos el 90 % se recupera con desviación de vértices menor a 2 mm de papel, y el número de polilíneas espurias de menos de 5 mm es cero tras el filtro. El umbral es para el conjunto sintético; sobre croquis reales se mide y reporta, no se exige.
- **Prueba:** generador de croquis sintéticos (líneas con jitter gaussiano, grosor variable, ruido de papel) y comparación con shapely.

### CU-04. Edición

- **Actor:** `technician` o `reviewer` con lease.
- **Herramientas mínimas:** seleccionar, mover vértice, dibujar línea/polilínea/punto/texto/cota, insertar símbolo del catálogo con rotación y atributos, reclasificar capa, eliminar, deshacer/rehacer, medir (sin crear entidad), **fijar longitud a cota** (el segmento se escala desde el vértice anclado), **fijar punto por dos distancias** (triangulación: el técnico elige dos puntos de referencia, ingresa dos distancias, el sistema calcula las dos intersecciones y preselecciona la más cercana a la posición dibujada), **fijar punto por base+offset** (línea base + distancia a lo largo + distancia perpendicular con lado), **OCR de recorte** (dibuja una caja sobre un número manuscrito; el worker devuelve valor y confianza; el técnico acepta o escribe el valor, que se asocia a la cota o al segmento seleccionado).
- **Snapping:** a extremos, a ortogonal (0°/90° respecto a los ejes locales) y a grilla configurable. Cada aplicación de snapping u ortogonalización se registra como evento con el usuario y la entidad.
- **Criterio:** una entidad editada conserva `source` original y añade historial; la triangulación con distancias incompatibles no mueve nada y explica por qué; deshacer restaura la geometría exacta.
- **Prueba:** tests unitarios de las funciones geométricas (intersección de circunferencias, base+offset, escalado anclado) con casos degenerados; test E2E del editor con Playwright sobre un plano de ejemplo.

### CU-05. Validación y bloqueo

- **Actor:** `reviewer`.
- **Flujo:** cambia el estado a `IN_REVIEW`, revisa, corrige si hace falta, pasa a `VALIDATED`. La revisión `draft` se congela en snapshot canónico con hash.
- **Criterio:** tras `VALIDATED` no se puede editar sin reabrir; reabrir crea una revisión nueva con `parent_revision_id`; el snapshot validado sigue recuperable byte a byte.
- **Prueba:** test de integración sobre la máquina de estados y el almacenamiento de revisiones.

### CU-06. Exportación

- **Actor:** `technician` (borrador) y `reviewer` (final).
- **Flujo borrador:** DXF y PDF generados desde la revisión de trabajo, con capa `L_SHEET` y marca de agua `DRAFT`, sin manifiesto, sin cambio de estado.
- **Flujo final:** desde la revisión validada: DXF, PDF técnico, reporte de auditoría PDF, `manifest.json` firmado, ZIP. Estado → `EXPORTED`.
- **Criterio:** el DXF pasa `ezdxf.audit()` sin errores, se relee con ezdxf y sus entidades coinciden en número, capa y coordenadas con el modelo canónico (tolerancia 1e-6 m); abre sin errores en FreeCAD (verificación manual local), LibreCAD y QCAD (CI donde sea viable); las tres distancias de control (horizontal, vertical, diagonal) coinciden en modelo, DXF y PDF dentro de la tolerancia. El PDF es reproducible: dos generaciones de la misma revisión tienen el mismo hash.
- **Prueba:** golden DXF por fixture; test que mide distancias en el PDF mediante extracción de vectores (pypdfium2 o pdfplumber); apertura en FreeCAD (manual, por hito) y en LibreCAD/QCAD en CI con `xvfb` donde sea viable.

### CU-07. Auditoría y verificación

- **Actor:** `reviewer`, auditor externo con el ZIP.
- **Flujo:** cada evento de dominio se escribe en `event` con `prev_hash` y `hash`. El manifiesto incluye hashes de todos los artefactos y de la cadena de eventos. Un script de verificación (`sketch2cad-verify`, incluido en el ZIP como instrucciones y disponible en el repo) recalcula hashes y verifica la firma sin acceso al servidor.
- **Criterio:** modificar un byte de cualquier artefacto o de un evento hace fallar la verificación con mensaje que identifica el artefacto.
- **Prueba:** test que genera un paquete, lo altera y verifica.

### CU-08. Fallos explícitos

- **Criterio:** imagen ilegible, calibración fuera de tolerancia y job fallido producen un estado visible con causa y acción posible. Nunca un resultado "aparentemente bien" por defecto.
- **Prueba:** tests de integración por cada camino de error.

## 7. Arquitectura y stack

### 7.1 Principio

Un backend modular en Python, un worker Python que comparte código con el backend, un frontend React. Un solo lenguaje de servidor porque todas las librerías críticas (OpenCV, shapely, ezdxf, ReportLab, TrOCR) son Python. Sin microservicios ni broker hasta que la carga o la organización lo exijan.

```text
┌──────────────────────────────┐
│ Frontend: React + TS + Vite  │  editor SVG, paleta, tablas, estados
└──────────────┬───────────────┘
               │ HTTPS / REST JSON (+ polling de jobs)
┌──────────────▼───────────────┐
│ Backend: FastAPI             │  auth, case files, plans, revisions,
│  módulos: auth, cases,       │  calibration, entities, exports,
│  sources, plans, calibration,│  audit, catalog, jobs
│  suggestions, exports, audit │
└───┬───────────┬──────────┬───┘
    │           │          │
┌───▼────┐ ┌────▼─────┐ ┌──▼──────────────────────────┐
│Postgres│ │ Storage  │ │ Worker (procrastinate)       │
│ datos, │ │ FS CAS   │ │  qc, rectify, suggest_lines, │
│ jobs,  │ │ (S3 opc.)│ │  ocr_crop, export_dxf,       │
│ events │ │          │ │  export_pdf, build_package    │
└────────┘ └──────────┘ └──────────────────────────────┘
```

### 7.2 Stack decidido

| Capa | Elección | Licencia | Notas |
|---|---|---|---|
| Frontend | React 18 + TypeScript + Vite; Zustand para estado; Immer para undo/redo por parches; pan/zoom del editor con el `viewBox` del SVG (`react-zoom-pan-pinch` solo en el visor de la fuente raster); editor en SVG | MIT | Sin Next.js, sin PWA. Editor en SVG por decisión del dueño; Konva solo si falla la medición de 60 fps con 2000 entidades. |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic | MIT | Un solo paquete `sketch2cad` con módulos; el worker importa el mismo paquete. |
| Cola | Procrastinate sobre PostgreSQL | MIT | Sin Redis ni RabbitMQ. |
| Base de datos | PostgreSQL 16 | PostgreSQL | Geometría en JSONB; sin PostGIS. |
| Almacenamiento | Interfaz `Storage`; driver `filesystem` direccionado por contenido (`objects/sha256/ab/cd/<hash>`), write-once a nivel de aplicación (ver §15); driver `s3` stub | — | MinIO descartado (AGPL). |
| Visión | OpenCV, scikit-image, sknw (o skan), shapely, numpy | Apache/BSD | Pipeline clásico, sin modelo entrenado. |
| OCR | `microsoft/trocr-base-handwritten` vía `transformers`, CPU | MIT / Apache | Solo sobre recortes marcados por el usuario. Modelo descargado en build, no en runtime. |
| DXF | ezdxf | MIT | Único generador. |
| PDF salida | ReportLab (`invariant=1`) | BSD | Dibujo directo a escala desde el modelo canónico. |
| PDF entrada | pypdfium2 | Apache/BSD | Rasterizar PDF de una página a 300 dpi. |
| Auth | Usuarios locales, `argon2-cffi`, sesión en cookie `HttpOnly` + CSRF | MIT | Interfaz `IdentityProvider` para OIDC futuro. |
| Firma | `cryptography` Ed25519; `rfc8785` para JSON canónico | Apache/BSD, MIT | Clave del servidor en archivo o variable de entorno; rotación documentada. |
| Despliegue | docker-compose: `db`, `api`, `worker`, `web` (nginx sirviendo el build) | — | Un host con cifrado de disco; clave de firma en archivo con permisos 600. |
| Repositorio | Uno solo, con dos carpetas: `sketch2cad-api/` (API, worker, migraciones, docker-compose) y `sketch2cad-web/` (editor) | — | Decisión del dueño (2026-10-25; antes eran dos repositorios). El cliente TypeScript se genera desde el esquema OpenAPI en CI para mantener el contrato. |
| Pruebas | pytest, hypothesis para geometría, Playwright para E2E, `pip-licenses` en CI | — | Lista blanca: MIT, BSD, Apache-2.0, PSF, LGPL. |

### 7.3 Política de procesamiento externo

Ningún dato de expediente sale del despliegue. No se llama a servicios externos de IA. La configuración `external_ai.enabled` existe y es `false`; activarla requiere además `external_ai.authorization_reference` no vacío (referencia al acto administrativo que lo autoriza) y queda en el log de auditoría.

## 8. Modelo de datos

### 8.1 Tablas

| Tabla | Campos principales |
|---|---|
| `user` | `id`, `username`, `display_name`, `password_hash`, `role`, `is_active`, `created_at` |
| `case_file` | `id` (UUID), `code` (único, legible), `incident_type` (`traffic_collision`, `crime_scene`, `inspection`, `other`), `incident_date`, `description`, `declared_location` (texto), `organization`, `status`, `retention_policy_id` (null), `legal_hold` (false), `created_by`, `created_at` |
| `case_member` | `case_file_id`, `user_id`, `permissions` (array) |
| `source` | `id`, `case_file_id`, `original_filename`, `media_type`, `byte_size`, `sha256`, `storage_key`, `width_px`, `height_px`, `dpi` (null), `exif` (JSONB), `qc` (JSONB: `status`, `checks[]`), `uploaded_by`, `uploaded_at` |
| `plan` | `id`, `case_file_id`, `title`, `primary_source_id`, `source_mode` (`scaled_drawing` / `schematic_sketch`), `status`, `metric_status`, `current_revision_id`, `validated_revision_id` (null), `edit_lease` (JSONB: `user_id`, `expires_at`), `created_at` |
| `calibration` | `id`, `plan_id`, `method` (`scan_dpi`, `scan_segment`, `photo_homography_4pt`, `none`), `image_to_paper` (matriz 3×3, mm por px), `control_points` (JSONB), `reprojection_error_mm` (`max`, `rms`), `paper_scale` (p. ej. `100` para 1:100; null en `schematic_sketch`), `local_frame` (JSONB: `origin_paper_mm`, `x_axis_angle_deg`, `north_angle_deg`), `accepted_by`, `accepted_at` |
| `revision` | `id`, `plan_id`, `number`, `parent_revision_id`, `state` (`draft` / `frozen`), `snapshot` (JSONB canónico, solo cuando `frozen`), `snapshot_sha256`, `created_by`, `created_at`, `frozen_at` |
| `evidence` | `id`, `case_file_id`, `number` (único por expediente), `label`, `attributes` (JSONB), `created_by`, `created_at` |
| `entity` | `id`, `revision_id`, `type`, `layer`, `geometry` (JSONB), `image_region` (JSONB, null), `attributes` (JSONB), `evidence_id` (null), `constraint` (JSONB, null), `source` (JSONB), `confidence` (float, null), `model_version` (null), `review_status`, `metric_origin`, `created_by`, `updated_at`, `deleted_at` |
| `job` | gestionado por Procrastinate más `job_meta`: `id`, `plan_id`, `kind`, `params`, `status`, `result_ref`, `error`, `model_version`, `started_at`, `finished_at` |
| `event` | `id` (bigserial), `case_file_id`, `plan_id`, `revision_id`, `actor_id`, `actor_role`, `kind`, `entity_id` (null), `before` (JSONB), `after` (JSONB), `reason` (null), `client_ip`, `occurred_at`, `prev_hash`, `hash` |
| `artifact` | `id`, `plan_id`, `revision_id`, `kind` (`dxf`, `pdf_plan`, `pdf_audit`, `manifest`, `zip`, `rectified_image`), `is_draft`, `sha256`, `storage_key`, `byte_size`, `generator_version`, `created_at` |
| `catalog` | `id`, `version`, `is_active`, `layers` (JSONB), `symbols` (JSONB), `created_at` |

### 8.2 Entidad (esquema JSON)

```json
{
  "id": "ent_01J...",
  "type": "line | polyline | point | text | dimension | symbol",
  "layer": "L_STRUCTURE",
  "geometry": {
    "kind": "polyline",
    "coords_m": [[0.0, 0.0], [4.2, 0.0], [4.2, 3.1]],
    "closed": false
  },
  "image_region": {
    "source_id": "src_...",
    "polygon_px": [[120, 340], [980, 338], [982, 900]]
  },
  "attributes": {},
  "evidence_id": null,
  "constraint": {
    "kind": "triangulation | baseline_offset | null",
    "refs": ["ent_A", "ent_B"],
    "params": { "d_a_m": 2.10, "d_b_m": 3.45, "solution": "nearest" }
  },
  "source": {
    "method": "manual | cv | ocr | model | derived",
    "job_id": "job_... | null",
    "parent_entity_id": "ent_... | null"
  },
  "confidence": null,
  "model_version": null,
  "review_status": "suggested | accepted | corrected | rejected",
  "metric_origin": "declared_dimension | triangulation | baseline_offset | scaled_drawing | derived | trace_only | none",
  "created_by": "user_... | system",
  "revision_number": 3
}
```

Reglas:

- `confidence` es `null` para entidades manuales; un número en [0, 1] para sugerencias.
- `geometry.coords_m` siempre en metros, en el sistema local, Y hacia arriba. Las sugerencias recién creadas que aún no tienen sistema local se guardan con `coords_m` provisionales obtenidos de imagen→papel→escala provisional y `metric_origin = "trace_only"`.
- Tipos y geometría: `line` y `polyline` → `coords_m[]`; `point` → `coords_m[1]`; `text` → `anchor_m`, `rotation_deg`, `height_mm_paper`, `content`; `dimension` → `p1_m`, `p2_m`, `offset_m`, `declared_value_m` (null si solo es medida), `measured_value_m`; `symbol` → `anchor_m`, `rotation_deg`, `scale`, `symbol_id`, `attributes` (incluye `evidence_number` si es indicio).
- Una `dimension` con `declared_value_m` distinto de `measured_value_m` más allá de la tolerancia se marca `discrepancy = true` y se dibuja con estilo de alerta. El sistema nunca corrige ninguno de los dos valores por su cuenta.
- `deleted_at` en vez de borrado físico mientras la revisión es `draft`; al congelar se excluyen del snapshot pero quedan en eventos.
- **Sin topología compartida.** **[decisión del autor]** Cada entidad tiene su propia geometría; no hay grafo de vértices compartidos. La conexión entre entidades se expresa solo por `constraint` (unidireccional) y por snapping al crear o mover. Esto mantiene H3 en cálculo puntual; un modelo de restricciones completo queda fuera del MVP.
- `metric_origin` para polilíneas: `declared_dimension` solo si **todos** sus segmentos tienen longitud declarada; si solo algunos, la polilínea es `trace_only` (y el plano, `approximate`). `derived`: línea o polilínea cuyos vértices coinciden (por snapping) con puntos cuyo origen es métrico. `none`: entidad colocada a mano sin cota, que cuenta como no métrica.

### 8.3 Snapshot canónico de revisión

```json
{
  "schema_version": "2.0",
  "plan_id": "...",
  "revision_number": 3,
  "source_mode": "schematic_sketch",
  "metric_status": "metric",
  "calibration": { "...": "copia de la fila calibration" },
  "local_frame": { "...": "..." },
  "catalog_version": "default-1",
  "entities": [ "ordenadas por id" ],
  "evidence_table": [
    { "evidence_id": "...", "evidence_number": 1, "entity_id": "...", "label": "...", "x_m": 2.10, "y_m": 3.45, "metric_origin": "triangulation" }
  ]
}
```

Serializado con RFC 8785 (JCS). `snapshot_sha256` es el hash de esos bytes.

## 9. API y máquina de estados

### 9.1 Endpoints principales

Prefijo `/api/v1`. Autenticación por sesión. Todas las respuestas de error siguen RFC 9457 (`application/problem+json`).

| Método y ruta | Uso |
|---|---|
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Sesión. |
| `GET/POST /case-files`, `GET/PATCH /case-files/{id}` | Expedientes. |
| `POST /case-files/{id}/members` | Autorizar usuarios. |
| `POST /case-files/{id}/sources` (multipart, con `client_sha256`) | Ingesta. Devuelve `source` con `qc`. |
| `GET /sources/{id}/original` | Descarga del original (auditada). |
| `GET /sources/{id}/preview?max=2048` | Vista previa reducida (derivado, no original). |
| `POST /case-files/{id}/plans` | Crear plano desde fuente en `CALIBRATION_PENDING`. No encola IA: `suggest_lines` se encola al aceptar la calibración o elegir `non_metric`. |
| `GET /plans/{id}` | Plano con estado, calibración, lease, revisión actual. |
| `PUT /plans/{id}/calibration` | Enviar puntos y método; devuelve error de reprojección y matriz. `POST /plans/{id}/calibration/accept`. |
| `PUT /plans/{id}/local-frame` | Origen, eje X, Norte. |
| `POST /plans/{id}/lease`, `DELETE /plans/{id}/lease` | Lease de edición. |
| `GET /revisions/{id}/entities` | Entidades de la revisión. |
| `POST /revisions/{id}/entities` | Crear una o varias. |
| `PATCH /entities/{id}`, `DELETE /entities/{id}` | Editar. Cuerpo incluye `reason` opcional y `operation` (`move_vertex`, `snap_orthogonal`, `fix_length`, `triangulate`, `baseline_offset`, `relabel`, ...). |
| `POST /revisions/{id}/entities:bulk-review` | `{ "ids": [...], "review_status": "accepted|rejected" }`. |
| `POST /plans/{id}/jobs` | `{ "kind": "suggest_lines" | "ocr_crop", "params": {...} }`. Devuelve `job`. |
| `GET /jobs/{id}` | Estado; el cliente hace polling cada 2 s. |
| `POST /plans/{id}/transition` | `{ "to": "REVIEW_PENDING" | "IN_REVIEW" | "VALIDATED" | "REOPENED" | "REJECTED", "reason": "..." }`. |
| `POST /plans/{id}/exports` | `{ "mode": "draft" | "final" }`. Encola; devuelve `job`. |
| `GET /plans/{id}/artifacts`, `GET /artifacts/{id}` | Listar y descargar (final requiere `case_file:deliver`; muestra manifiesto antes de confirmar con `?confirm=true`). |
| `GET/POST /case-files/{id}/evidence`, `PATCH /evidence/{id}` | Registro de indicios del expediente (numeración única). |
| `GET /case-files/{id}/events?plan_id=` | Log de auditoría. |
| `GET /catalog/active` | Capas y símbolos (SVG inline). |
| `GET/POST /admin/users`, `POST /admin/catalog` | Administración. |

Payload de sugerencias (resultado de `suggest_lines`): lista de entidades con `review_status = "suggested"`, `source.method = "cv"`, `image_region` y `coords_m` provisionales; el backend las inserta en la revisión `draft` actual en una sola transacción y emite un evento `suggestions_added` con el conteo.

### 9.2 Estados del plano

```text
CREATED → CALIBRATION_PENDING → EDITING → REVIEW_PENDING → IN_REVIEW → VALIDATED → EXPORTED
                 ↑                 ↑                            │           │
                 └─────────────────┴──────── REOPENED ◄─────────┴───────────┘
IN_REVIEW → REJECTED (vuelve a EDITING con motivo)
```

| Transición | Quién | Efecto |
|---|---|---|
| `CREATED → CALIBRATION_PENDING` | sistema al crear plano | — |
| `CALIBRATION_PENDING → EDITING` | `technician` al aceptar calibración o elegir `non_metric` | Genera imagen rectificada y encola `suggest_lines`. |
| `EDITING → REVIEW_PENDING` | `technician` | Libera lease. |
| `REVIEW_PENDING → IN_REVIEW` | `reviewer` | Toma lease. |
| `IN_REVIEW → VALIDATED` | `reviewer` | Congela revisión: snapshot + hash. `validated_revision_id`. |
| `IN_REVIEW → REJECTED → EDITING` | `reviewer` | Motivo obligatorio. |
| `VALIDATED → EXPORTED` | `reviewer` al completar exportación final | Artefactos + manifiesto. |
| `VALIDATED|EXPORTED → REOPENED → EDITING` | `reviewer` | Nueva revisión `draft` con `parent_revision_id`; artefactos anteriores quedan. |

`suggest_lines` corre una vez aceptada la calibración (o elegido `non_metric`) para trabajar sobre la imagen rectificada; el encolado es idempotente por plano y calibración (un segundo `accept` no crea otro job); el técnico puede relanzarlo mientras no haya entidades aceptadas. `ocr_crop` no depende de la calibración. Las exportaciones borrador se permiten desde `EDITING` en adelante; la final solo desde `VALIDATED`.

## 10. Pipeline de procesamiento

Todas las etapas corren en el worker, con `model_version`/`pipeline_version` y parámetros registrados en `job_meta`. Toda salida es `suggested`.

| Etapa | Entrada | Método | Salida | Parámetros por defecto |
|---|---|---|---|---|
| `qc` | original | lado corto en px; varianza del Laplaciano sobre gris; rango de contraste (p5–p95); detección de recorte (bordes de papel tocando el borde) | `status` (`green`/`amber`/`red`) y lista de checks | mínimo lado corto 1500 px (amber bajo 1500, red bajo 900); Laplaciano var < 100 amber, < 40 red; contraste < 60 niveles amber |
| `rectify` | original + homografía o DPI | `cv2.warpPerspective` a un lienzo en mm de papel a 10 px/mm | `rectified_image` (derivado almacenado como artefacto) | — |
| `suggest_lines` | imagen rectificada (original si `non_metric`); se encola al aceptar la calibración | gris → `cv2.adaptiveThreshold` (Gaussian, bloque 31, C 10) → apertura morfológica 3×3 → `skeletonize` → grafo con `sknw` → caminos entre nodos → `shapely.simplify` con tolerancia 0,8 mm papel → descartar < 5 mm → unir colineales con ángulo < 5° | polilíneas `suggested`, `confidence = f(longitud, rectitud)` | tolerancias en configuración |
| `ocr_crop` | recorte en px definido por el usuario | TrOCR handwritten; normalización: coma → punto, quitar "m"/"mts"; validación numérica | `{ "text": "3.50", "value_m": 3.5, "confidence": 0.82 }` o `{ "value_m": null }` | confianza mínima para preseleccionar: 0,6 |
| `export_dxf` | snapshot | ezdxf | `dxf` | ver §13 |
| `export_pdf` | snapshot + rectified_image | ReportLab | `pdf_plan` | ver §13 |
| `build_package` | artefactos finales | manifiesto, firma, ZIP | `manifest`, `pdf_audit`, `zip` | ver §14 |

Coordenadas de salida de `suggest_lines`: píxeles de la imagen rectificada → mm de papel (÷10) → metros: si `scaled_drawing`, `mm × paper_scale / 1000`; si `schematic_sketch`, se usa una escala provisional de 1:100 solo para que el dibujo tenga tamaño razonable, y `metric_origin = "trace_only"`. El plano no puede ser `metric` mientras exista geometría estructural `trace_only`.

## 11. Calibración y metrología

### 11.1 Transformaciones

- **imagen→papel:** matriz 3×3 (homografía) o escala uniforme. Se guarda completa con los puntos de control.
- **papel→mundo:** solo en `scaled_drawing`: `m = mm × paper_scale / 1000`. La escala la declara el técnico y se verifica contra al menos una cota conocida; una discrepancia mayor a la tolerancia impide aceptar la calibración como `metric`.
- **Sistema local:** origen (punto del plano), ángulo del eje +X, ángulo del Norte respecto a +Y (positivo antihorario). El modelo no se rota al cambiar el Norte; solo cambia el símbolo y el metadato.
- **Convención de ejes:** imagen Y hacia abajo; papel Y hacia abajo (mm); mundo Y hacia arriba. La conversión papel→mundo invierte Y. DXF y PDF usan mundo.
- **Cambios posteriores.** **[decisión del autor]** La calibración imagen→papel se bloquea cuando existe la primera entidad aceptada o manual; recalibrar exige descartar las entidades o crear otro plano. El sistema local sí puede cambiarse en cualquier momento: el cambio de origen o de eje +X aplica una transformación rígida (traslación + rotación) a `coords_m` de todas las entidades de la revisión `draft` en una transacción, con un único evento `local_frame.changed` que guarda la transformación; las cotas declaradas y las restricciones no cambian porque son invariantes a movimientos rígidos. Cambiar el Norte solo cambia el metadato y el símbolo.

### 11.2 Métodos admitidos en MVP

| Método | Cuándo | Entrada | Error reportado |
|---|---|---|---|
| `scan_dpi` | escaneo con DPI en metadatos | nada | ninguno (se advierte que depende del metadato) |
| `scan_segment` | escaneo sin DPI o para verificar | 2 puntos + longitud en papel (mm) | diferencia con DPI si existe |
| `photo_homography_4pt` | fotografía | 4 puntos + dimensiones del rectángulo (mm) | error de reproyección máx y RMS (mm) |
| `none` | sin referencia | nada | plano `non_metric` |

Control múltiple con N puntos y ajuste por mínimos cuadrados: diferido. Los puntos adicionales que el técnico marque se guardan y se informa su error, pero no participan del ajuste.

### 11.3 Herramientas métricas del editor (reconstrucción por cotas)

| Herramienta | Entrada | Cálculo | `metric_origin` |
|---|---|---|---|
| Fijar longitud | segmento, vértice ancla, valor | escala el segmento desde el ancla manteniendo dirección | `declared_dimension` |
| Triangulación | punto P a fijar, referencias A y B con `metric_origin` ≠ `trace_only`, distancias dA y dB | intersección de circunferencias; 0, 1 o 2 soluciones; se preselecciona la más cercana a la posición actual de P; el técnico confirma | `triangulation` |
| Base + offset | línea base (dos puntos métricos), distancia a lo largo s, offset perpendicular d, lado | P = A + s·û + d·n̂ | `baseline_offset` |
| Cota declarada | dos puntos, valor | crea `dimension` con `declared_value_m`; si difiere de la medida se marca discrepancia | — |

Comportamiento geométrico **[decisión del autor]**:

- **Fijar longitud** sobre el segmento AB de una polilínea con ancla en A mueve B y, rígidamente, todos los vértices del mismo lado de la polilínea (estiramiento tipo CAD). Las demás entidades no se mueven solas; las que tengan una `constraint` que referencie a esa polilínea se recalculan.
- **Triangulación y base+offset** guardan la `constraint` (referencias y distancias). Si una referencia se mueve, la entidad dependiente se recalcula y se emite un evento `entity.recomputed`. Las referencias deben tener `metric_origin` métrico y no pueden depender, directa ni indirectamente, de la entidad que se está fijando.
- Mover a mano una entidad con `constraint` elimina la restricción tras confirmación y degrada `metric_origin` a `none`.

Un plano es `metric` cuando **todas** las entidades en `L_STRUCTURE` y `L_ACCESS` y todas las que referencian un indicio tienen `metric_origin` en {`declared_dimension`, `triangulation`, `baseline_offset`, `scaled_drawing`, `derived`}. Cualquier `trace_only` o `none` en esas capas lo deja en `approximate`. Ciclo de `metric_origin`: toda sugerencia nace `trace_only` y nunca se promueve sola. Solo cuando una persona la acepta (`review_status = accepted` o `corrected`), el plano está en modo `scaled_drawing` y la calibración está aceptada como métrica, la entidad pasa a `scaled_drawing`; las entidades dibujadas a mano en ese modo reciben `scaled_drawing` al crearse. En `schematic_sketch` no hay promoción: la entidad sigue `trace_only` hasta que una cota la haga `declared_dimension`. Se recalcula en cada cambio y el editor muestra qué entidades impiden el estado `metric`.

### 11.4 Tolerancias configurables

| Parámetro | Default | Ámbito |
|---|---|---|
| `calibration.max_reprojection_error_mm` | 1,0 | aceptar homografía como métrica |
| `calibration.max_scale_discrepancy_pct` | 1,0 | verificar `paper_scale` |
| `dimension.discrepancy_tolerance_m` | 0,02 | marcar discrepancia declarada vs medida |
| `export.control_distance_tolerance_pct` | 0,5 | prueba CA de escala |
| `suggestions.min_confidence_highlight` | 0,7 | resaltar baja confianza |

Por tipo de hecho (`incident_type`) se pueden sobreescribir.

## 12. Catálogo de capas y símbolos

### 12.1 Capas (nomenclatura única; las de las specs anteriores quedan obsoletas)

| Capa | Uso | Color DXF (índice ACI) |
|---|---|---|
| `L_STRUCTURE` | muros, bordes de calzada, límites | 7 |
| `L_ACCESS` | puertas, ventanas, accesos | 4 |
| `L_EVIDENCE` | indicios y objetos documentados (puntos y símbolos de indicio) | 1 |
| `L_SYMBOLS` | símbolos no indiciarios (vehículo genérico, mobiliario, semáforo) | 3 |
| `L_DIMENSIONS` | cotas | 2 |
| `L_TEXT` | etiquetas y notas | 7 |
| `L_ORIENTATION` | Norte, ejes locales | 5 |
| `L_ANNOTATIONS` | observaciones del técnico o revisor | 6 |
| `L_CONTROL` | puntos de control y calibración | 8 |
| `L_SHEET` | cajetín, marco, leyenda, marca de agua (solo en exportación) | 7 |

### 12.2 Formato de símbolo

```json
{
  "symbol_id": "evidence_marker",
  "label": "Marcador de indicio",
  "default_layer": "L_EVIDENCE",
  "svg": "<svg viewBox='-10 -10 20 20'>...</svg>",
  "dxf_block": { "entities": [ "primitivas en mm o en m según size_mode" ] },
  "size_mode": "paper_mm | world_m",
  "default_size": 6,
  "attributes": [
    { "name": "evidence_number", "type": "integer", "required": true },
    { "name": "label", "type": "string" }
  ]
}
```

`size_mode = paper_mm` (anotativo): el símbolo mide lo mismo en papel a cualquier escala; `world_m`: mide en metros reales (vehículo genérico 4,5 × 1,8 m). En DXF los anotativos se insertan con escala = `paper_mm × paper_scale / 1000`.

### 12.3 Catálogo por defecto del MVP

`north_arrow` (paper_mm, `L_ORIENTATION`), `evidence_marker` (paper_mm, `L_EVIDENCE`, atributo `evidence_number`), `control_point` (paper_mm, `L_CONTROL`), `door` (world_m, ancho editable, `L_ACCESS`), `window` (world_m, `L_ACCESS`), `generic_vehicle` (world_m 4,5 × 1,8, `L_SYMBOLS`, atributo `label`), `traffic_light` (paper_mm, `L_SYMBOLS`), `tree` (paper_mm, `L_SYMBOLS`). **[decisión del autor]** Nada que represente víctimas, sangre, armas o casquillos en el catálogo por defecto; una institución los agrega con su propia simbología.

## 13. Exportación

### 13.1 DXF

- ezdxf, `setup=True`, versión `R2013` (AC1027). `doc.units = ezdxf.units.M`; `$INSUNITS = 6`; `$MEASUREMENT = 1`.
- Espacio modelo 1:1 en metros, sistema local, Y hacia arriba.
- Capas del catálogo con su color; nada se crea ad hoc.
- `line` → `LINE`; `polyline` → `LWPOLYLINE`; `point` → `POINT` más `INSERT` del marcador si es indicio; `text` → `TEXT` con estilo `S2C_TEXT` (altura = `height_mm_paper × paper_scale / 1000`); `dimension` → `DIMENSION` lineal alineada con `render()`, texto sobreescrito con el valor declarado cuando existe; `symbol` → `INSERT` de bloque `S2C_<symbol_id>` con `ATTRIB` por atributo.
- Metadatos en `XDATA` de una entidad invisible en `L_SHEET` o en `$CUSTOMPROPERTYTAG`: `plan_id`, `revision_number`, `snapshot_sha256`, `metric_status`, `generator_version`.
- Borrador: texto `DRAFT — NOT VALIDATED` en `L_SHEET`.
- Validación: `doc.audit()` sin errores; relectura con ezdxf y comparación con el snapshot; apertura en FreeCAD por hito y en LibreCAD y QCAD en CI donde sea viable.
- Reproducibilidad byte a byte (spike 2, `docs/spikes/dxf-reproducibility.md`):
  - versiones fijadas de ezdxf (1.4.5) y Python (3.12); actualizarlas exige regenerar los hashes de referencia;
  - variables de cabecera deterministas: `$TDCREATE`, `$TDUCREATE`, `$TDUPDATE` y `$TDUUPDATE` desde `frozen_at` en UTC, `$TDINDWG` y `$TDUSRTIMER` en 0, `$FINGERPRINTGUID` y `$VERSIONGUID` derivados con `uuid5` del `plan_id` y del `snapshot_sha256`, marcadores de ezdxf con `frozen_at` y `CLASSES` ordenadas;
  - escritura como bytes UTF-8 con `\n` y guardado en modo binario.

### 13.2 PDF técnico

- ReportLab con `invariant=1`, sin fecha de creación embebida variable; metadatos del documento con `plan_id` y `revision_number`.
- Hoja A4 o A3 (horizontal por defecto). Escala elegida entre 1:50, 1:100, 1:200, 1:250, 1:500: el sistema propone la mayor escala en la que el plano cabe con margen de 15 mm; el técnico puede cambiarla. Nunca "ajustar a página".
- Contenido: plano a escala; marco; cajetín con `case_file.code`, título, tipo de hecho, fecha, técnico, revisor, revisión, `metric_status` y `snapshot_sha256` abreviado; escala numérica y gráfica; flecha de Norte; leyenda de símbolos usados; tabla de indicios (número, etiqueta, X, Y, origen métrico); anexo con la imagen original (o rectificada) en página aparte con su hash.
- Borrador: marca de agua `BORRADOR / DRAFT` diagonal y sin tabla de hashes.
- Comprobación: una línea de 1 m a 1:100 mide exactamente 10 mm (28,35 pt) en el PDF; test automatizado extrae los vectores con pypdfium2/pdfplumber.

## 14. Auditoría, hashes, manifiesto y firma

### 14.1 Eventos

Toda acción de dominio produce una fila en `event`. Tipos mínimos: `case_file.created`, `member.added`, `source.ingested`, `source.downloaded`, `plan.created`, `calibration.proposed`, `calibration.accepted`, `local_frame.set`, `local_frame.changed`, `suggestions.added`, `evidence.created`, `evidence.updated`, `entity.created`, `entity.updated` (con `operation`), `entity.recomputed`, `entity.reviewed`, `entity.deleted`, `plan.transition`, `revision.frozen`, `export.requested`, `artifact.created`, `artifact.downloaded`, `lease.acquired`, `lease.released`, `auth.login`, `auth.failed`, `access.denied`, `config.changed`.

Encadenamiento: `hash = SHA-256(prev_hash || JCS(event sin hash))`. El primer evento de cada expediente usa `prev_hash = SHA-256(case_file_id)`. Escritura en la misma transacción que el cambio.

### 14.2 Manifiesto

```json
{
  "manifest_version": "2.0",
  "case_file": { "id": "...", "code": "..." },
  "plan_id": "...",
  "revision": { "number": 3, "snapshot_sha256": "..." },
  "artifacts": [
    { "kind": "source_original", "filename": "...", "sha256": "..." },
    { "kind": "dxf", "filename": "plan.dxf", "sha256": "..." },
    { "kind": "pdf_plan", "filename": "plan.pdf", "sha256": "..." },
    { "kind": "pdf_audit", "filename": "audit.pdf", "sha256": "..." }
  ],
  "events_chain": { "count": 214, "last_hash": "..." },
  "calibration": { "...": "..." },
  "local_frame": { "...": "..." },
  "metric_status": "metric",
  "software": { "version": "2.0.0", "git_commit": "...", "pipeline_version": "...", "ocr_model": "trocr-base-handwritten@<hash>" },
  "catalog_version": "default-1",
  "validated_by": { "user_id": "...", "display_name": "...", "role": "reviewer" },
  "validated_at": "2026-10-24T15:04:05Z",
  "exported_by": { "...": "..." },
  "exported_at": "...",
  "timezone_display": "America/Panama",
  "signature": {
    "scheme": "ed25519-jcs-v1",
    "public_key_id": "server-key-2026-01",
    "value": "base64..."
  }
}
```

La firma se calcula sobre los bytes JCS del manifiesto sin el campo `signature`. El ZIP contiene `manifest.json`, los artefactos, el original, `VERIFY.md` con las instrucciones y la clave pública.

**[decisión del autor]** La firma Ed25519 con clave del servidor prueba que el paquete salió de esa instalación sin alteración; no sustituye la firma personal del perito ni un sello de tiempo. El reporte de auditoría lo dice con esas palabras.

### 14.3 Reporte de auditoría (PDF)

Representación legible del manifiesto más la lista de eventos del plano (quién, qué, cuándo, antes/después resumido), la nota de validez (§21) y las instrucciones de verificación.

## 15. Seguridad del MVP

- TLS terminado en nginx; cookies `Secure`, `HttpOnly`, `SameSite=Lax` (decisión del dueño, 2026-10-24; antes `Strict`); token CSRF obligatorio en toda mutación (`POST`, `PUT`, `PATCH`, `DELETE`), sin excepción.
- Contraseñas con Argon2id; bloqueo temporal tras 10 intentos fallidos; eventos `auth.failed`.
- Autorización por expediente en cada endpoint; intentos denegados registrados (`access.denied`).
- Límite de subida 50 MB; validación de tipo por contenido (magic bytes), no por extensión; PDF rasterizado en el worker, nunca interpretado por el navegador.
- Sin imágenes ni geometría en logs de aplicación; solo identificadores.
- Secretos por variables de entorno; clave de firma fuera del repositorio.
- Almacenamiento: la inmutabilidad es de aplicación. La API nunca expone reemplazo ni borrado de objetos, el driver crea cada objeto con permisos de solo lectura y no sobrescribe uno existente. Un proceso con acceso al volumen o al host puede alterarlos; esa amenaza se cubre con el cifrado de disco, los permisos del host y la verificación por hash (manifiesto y `sketch2cad-verify`), no con el almacenamiento. Un driver S3 con Object Lock es la mitigación para la fase institucional.
- Dependencias con versiones fijadas y escaneo de licencias y vulnerabilidades en CI.
- Diferido con punto de extensión: MFA, OIDC, cifrado en reposo gestionado, legal hold, retención, backups con restauración probada. Para el MVP basta con cifrado de disco del host y un `pg_dump` + copia del directorio de objetos programados y documentados.

## 16. Criterios de aceptación y pruebas

| CA | Criterio | Prueba |
|---|---|---|
| CA-01 Original | El original se conserva y su SHA-256 se verifica después. | Integración: carga, descarga, compara. |
| CA-02 Calibración | Fotografía oblicua de patrón conocido rectificada dentro de 1 mm; sin rectificar no puede ser `metric`. | Fixture sintético con `warpPerspective`. |
| CA-03 Reconstrucción por cotas | Con un croquis esquemático y cotas conocidas, las coordenadas de indicios por triangulación y base+offset coinciden con las esperadas dentro de 1 cm. | Unitario con casos resueltos a mano; propiedad con hypothesis. |
| CA-04 Sugerencias | Sobre el conjunto sintético, ≥ 90 % de segmentos > 20 mm recuperados con desviación < 2 mm papel; cero espurios < 5 mm. | Generador sintético + shapely. |
| CA-05 Revisión humana | Una sugerencia puede aceptarse, corregirse, rechazarse o reemplazarse; la procedencia y el historial se conservan. | Integración sobre entidades y eventos. |
| CA-06 DXF | Pasa `audit()`, se relee igual al snapshot, abre en FreeCAD, LibreCAD y QCAD. | Golden + relectura; apertura por hito. |
| CA-07 Escala | Tres distancias de control coinciden en modelo, DXF y PDF dentro de 0,5 %. | Test que extrae vectores del PDF y entidades del DXF. |
| CA-08 Reproducibilidad | Dos exportaciones de la misma revisión producen DXF y PDF con el mismo hash. | Test de doble generación. |
| CA-09 Auditoría | Alterar un byte de un artefacto o evento hace fallar la verificación nombrando el artefacto. | Test de paquete alterado. |
| CA-10 Estados | Transiciones no permitidas son rechazadas; `VALIDATED` congela; reabrir crea revisión nueva y conserva la anterior. | Integración sobre máquina de estados. |
| CA-11 Fallos | Imagen ilegible, calibración fuera de tolerancia, job fallido → estado explícito con causa y acción. | Integración por camino de error. |
| CA-12 Seguridad | Un usuario no autorizado no puede leer, editar, exportar ni descargar; cada intento queda registrado. | Integración con dos usuarios. |
| CA-13 Licencias | Ninguna dependencia fuera de la lista blanca. | `pip-licenses` y `license-checker` en CI. |

CA-04, CA-05 y la medición de G-3 dependen de H5. Si H5 se recorta, el MVP cierra con los demás criterios y esos tres quedan como validación posterior; el informe de H6 lo declara.

Datos de prueba: solo croquis sintéticos y croquis dibujados a propósito para el proyecto, sin datos reales de expedientes.

## 17. Requisitos no funcionales

- Fechas en UTC en almacenamiento y manifiesto; zona horaria de visualización configurable.
- Interfaz en español; unidades métricas; entrada numérica acepta coma y punto; el modelo y los archivos usan punto.
- Navegadores: Chrome, Edge, Firefox y Safari actuales en PC y tablet. Sin optimización táctil específica en MVP.
- `suggest_lines` sobre una imagen de 4000 × 3000 px termina en menos de 30 s en CPU de 4 núcleos; `ocr_crop` en menos de 3 s. El cliente muestra progreso o estado, nunca espera indefinida.
- El editor mantiene 60 fps con 2000 entidades en SVG en un portátil medio; si no, se cambia a Konva. El editor es SVG por decisión del dueño (2026-10-24); el spike 1 es solo esa medición. Los fps se miden al límite de refresco de la pantalla: en una de 60 Hz la mediana queda cerca de 59,9 aunque no se pierda ningún frame.
- Reproducibilidad: cada artefacto indica versión de código, pipeline, modelo y catálogo.
- Respaldo: procedimiento documentado de `pg_dump` + objetos; prueba de restauración manual al cierre de cada hito.

## 18. Plan de implementación

| Hito | Contenido | Resultado verificable |
|---|---|---|
| H0 (1 semana) | Repositorio, docker-compose, esqueleto FastAPI + React, migraciones, auth local, CI con pruebas y licencias | Login, expediente vacío, CI verde |
| H1 (2 semanas) | Expedientes, ingesta con hash y QC, Storage CAS, eventos encadenados, viewer de fuente | CA-01, CA-12 parcial |
| H2 (3 semanas) | Calibración (3 métodos), sistema local, editor SVG con herramientas básicas, lease, undo/redo, catálogo por defecto, revisiones | CA-02, CA-10 |
| H3 (2 semanas) | Herramientas métricas: fijar longitud, triangulación, base+offset, cotas con discrepancia, tabla de indicios, `metric_status` | CA-03 |
| H4 (3 semanas) | Exportación DXF y PDF (borrador y final), manifiesto, firma, ZIP, reporte de auditoría, script de verificación | CA-06, CA-07, CA-08, CA-09 |
| H5 (2 semanas) | Worker: `suggest_lines`, `ocr_crop`, aceptación por lote, resaltado por confianza | CA-04, CA-05 |
| H6 (2 semanas) | Endurecimiento, validación interna sobre ≥ 10 croquis dibujados a mano, medición de H1–H3, ajustes | Informe de hipótesis con alcance interno declarado |

Orden deliberado: el valor métrico (H3) y la exportación (H4) van antes que la IA (H5). Si el tiempo se acorta, el producto sin H5 sigue siendo útil.

**Condiciones confirmadas por el dueño del proyecto (2026-10-24):**

- **Equipo: una persona a tiempo completo.** Los hitos son secuenciales y las 15 semanas son la estimación base. Consecuencias: dos comprobaciones se adelantan a H0 para no descubrir problemas en H4 (medición de rendimiento del editor SVG con 2000 entidades; reproducibilidad byte a byte del DXF con ezdxf: handles, `$TDCREATE`, GUIDs). La administración de usuarios y catálogo queda en API más datos semilla, sin UI, hasta después del MVP.
- **Sin técnicos planimetristas disponibles por ahora.** H6 no puede medir las hipótesis H1–H3 con usuarios reales. Se redefine como **validación interna**: el desarrollador prepara un conjunto de al menos 10 croquis dibujados a mano siguiendo la práctica documentada (croquis esquemático con cotas, triangulaciones desde dos esquinas, línea base + offset en vía) y un patrón impreso fotografiado para CA-02, y mide tiempos, coincidencia de cotas y tasa de aceptación de sugerencias sobre ese conjunto. El informe de H6 declara explícitamente que la medición es interna y que la validación con técnicos queda pendiente. Los parámetros de `suggest_lines`, QC y OCR se exponen en configuración desde H5 porque se ajustarán cuando aparezcan croquis reales. Si en algún momento se consigue acceso a técnicos, se inserta una ronda de observación de 1 semana antes de dar por cerrado el MVP.
- **Decisiones de herramientas y organización (2026-10-24, PRD `h0-foundation`).** Visor CAD local: FreeCAD. Editor en SVG. Un solo repositorio con dos carpetas (`sketch2cad-api/`, `sketch2cad-web/`). Licencias: lista blanca MIT, BSD, Apache-2.0, PSF y LGPL, con excepción documentada por cada LGPL. CI en GitHub Actions. G-1 y G-3 se miden y reportan en v1, sin umbral. Mientras no haya revisión jurídica, el reporte y el PDF final llevan la leyenda "Borrador técnico, no apto como prueba".
- **Resultados de los spikes de H0 (2026-10-25).** Spike 1 (`docs/spikes/svg-performance.md`): con 2000 entidades el editor SVG da `median_fps` 144,93 (tope de la pantalla de 144 Hz) y `p95_frame_ms` 8,3 en Apple M4 Pro con Chromium con ventana; se mantiene SVG y la cámara del editor usa el `viewBox` (o una transformación de grupo), porque la transformación CSS del contenedor rompe `vector-effect: non-scaling-stroke`. Spike 2 (`docs/spikes/dxf-reproducibility.md`): el DXF es reproducible byte a byte con los ajustes de §13.1.

## 19. Parámetros que la institución puede fijar (todos con default)

| Parámetro | Default del MVP |
|---|---|
| Tolerancias (§11.4) | valores de la tabla |
| Catálogo de capas y símbolos | `default-1` (§12) |
| Formatos y versión DXF | R2013 |
| Escalas de PDF permitidas y hoja | 1:50…1:500, A4/A3 horizontal |
| Roles y permiso `case_file:deliver` | `reviewer` |
| Zona horaria de visualización | `UTC` |
| Política de procesamiento externo | deshabilitado |
| Retención y legal hold | sin lógica (campos presentes) |
| Mecanismo de firma | Ed25519 servidor |
| Membrete del cajetín | texto configurable sin logo |

## 20. Riesgos y mitigaciones

Ver `docs/evaluation/Evaluacion_especificaciones.md` §7. Los nueve riesgos ahí listados tienen su mitigación implementada en las secciones 4, 10, 11, 13, 14, 15 y 16 de este documento.

## 21. Nota de validez

Sketch2CAD es una herramienta de documentación y asistencia técnica. La validez probatoria, la admisibilidad del resultado, el régimen de firma y la cadena de custodia deben ser revisados y aprobados por la autoridad jurídica e institucional competente antes de usar el sistema en producción. El hash y la firma del paquete prueban integridad y origen técnico; no prueban por sí solos la autoría pericial ni la custodia física.

## 22. Glosario breve

- **Croquis esquemático:** dibujo de campo no a escala cuyas medidas reales están en las cotas anotadas.
- **Triangulación:** fijar un punto por sus distancias a dos puntos de referencia conocidos.
- **Base + offset (coordenadas rectangulares):** fijar un punto por su distancia a lo largo de una línea de referencia y su distancia perpendicular a ella.
- **Homografía:** transformación planar que corrige la perspectiva de una fotografía de un plano.
- **JCS (RFC 8785):** serialización canónica de JSON para que el mismo contenido produzca siempre los mismos bytes.
- **CAS (content-addressed storage):** almacenamiento donde la clave del objeto es su hash; hace imposible sobrescribir un original sin cambiar la clave.

## 23. Referencias

- OpenCV, homografía: https://docs.opencv.org/4.x/d9/dab/tutorial_homography.html
- scikit-image `skeletonize`: https://scikit-image.org/docs/stable/auto_examples/edges/plot_skeleton.html
- ezdxf: https://ezdxf.readthedocs.io/en/stable/
- ReportLab: https://docs.reportlab.com/
- Procrastinate: https://procrastinate.readthedocs.io/
- TrOCR handwritten: https://huggingface.co/microsoft/trocr-base-handwritten
- RFC 8785 (JCS): https://www.rfc-editor.org/rfc/rfc8785
- RFC 9457 (Problem Details): https://www.rfc-editor.org/rfc/rfc9457
- NIST, cadena de custodia: https://csrc.nist.gov/glossary/term/chain_of_custody
- Ultralytics, licencia (motivo de exclusión): https://www.ultralytics.com/license
