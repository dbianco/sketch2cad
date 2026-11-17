# Evaluación de las especificaciones de Sketch2CAD

**Fecha:** 2026-10-24
**Documentos evaluados:**

| Documento | Rol |
|---|---|
| `docs/specs/archive/forensicsketch_to_cad_parte_1.md` | Spec original, parte 1 (visión, arquitectura, stack, CU-01..05) |
| `docs/specs/archive/forensicsketch_to_cad_parte_2.md` | Spec original, parte 2 (casos de uso detallados) |
| `docs/specs/archive/Sketch2CAD_v1.md` v1.0 | Spec consolidada derivada de las dos anteriores |
| `docs/references/document.pdf` | Artículo "La IA en la disciplina de Planimetría Forense" (Semilla Científica nº 8, UMECIT, Panamá) |

**Resultado:** se produce una nueva spec, `docs/specs/Sketch2CAD_MVP.md` (v2.0), que resuelve los hallazgos de este reporte. Cada hallazgo indica la sección de esa spec que lo cierra.

**Decisiones de alcance acordadas antes de la evaluación:**

- MVP = vertical slice manual completo + IA mínima (sugerencia de líneas y OCR asistido de cotas). Sin detector de símbolos entrenado.
- Stack sin restricción institucional; se elige el que menos piezas móviles tenga.
- Spec genérica: tolerancias, catálogo, roles y firma son parámetros configurables con valores por defecto.

---

## 1. Resumen ejecutivo

1. **docs/specs/archive/Sketch2CAD_v1.md ya corrige la mayoría de los problemas de las specs originales.** Microservicios prematuros, Three.js para 2D, PostGIS innecesario, escala `px/m` global aplicada a fotografías oblicuas, umbral "80%" sin definición, YOLO sin dataset, confusión entre hash y cadena de custodia. Esos aciertos se conservan en la spec MVP.

2. **Persiste un error conceptual en los tres documentos: tratan el croquis a mano alzada como si fuera métrico.** Un croquis de campo no está a escala. La verdad métrica está en las cotas escritas y en las mediciones de referencia (triangulación desde dos puntos fijos, línea base + offset). Calibrar píxeles y luego "medir sobre el canvas" produce distancias falsas con apariencia de precisión, que es exactamente el antiobjetivo que Sketch2CAD declara en §3.3. La spec MVP separa dos modos de fuente y añade herramientas de posicionamiento por cotas. Es el cambio de mayor impacto y no requiere IA.

3. **docs/specs/archive/Sketch2CAD_v1.md mezcla requisitos de MVP con requisitos de operación institucional** (MFA, legal hold, Object Lock, pruebas de restauración, separación custodio/aprobador, firma institucional). La spec MVP los difiere explícitamente y deja los puntos de extensión.

4. **Hay decisiones de stack sin cerrar o con problemas de licencia.** Java vs Python queda abierto; YOLO (Ultralytics), MinIO y PyMuPDF son AGPL-3.0; Tesseract y PaddleOCR no sirven para manuscrito. La spec MVP fija Python end-to-end, una lista blanca de licencias y herramientas concretas.

5. **Faltan piezas para que un equipo pueda construir:** modelo de datos, API, semántica de la cota, convención de coordenadas, formato del catálogo, reglas concretas de DXF y PDF, cola de trabajos, concurrencia, estrategia de pruebas. La spec MVP las define.

---

## 2. Lo que docs/specs/archive/Sketch2CAD_v1.md ya resolvió bien

Se lista para no repetirlo y para que no se pierda en la nueva versión.

| Problema en las specs originales | Corrección en Sketch2CAD v1.0 | Se conserva en MVP |
|---|---|---|
| Microservicios Java + Python + broker para un equipo pequeño (parte 1 §2.1) | Backend modular + worker; broker solo con carga real (§6.1) | Sí, y se cierra la decisión en Python |
| Three.js para un editor 2D (parte 1 §2.2) | SVG; Canvas solo si rinde mal (§6.2) | Sí |
| PostGIS obligatorio (parte 1 §2.2) | Opcional, solo con CRS real (§6.2) | Sí, se quita del MVP |
| Relación `px/m` única para cualquier imagen (parte 1 CU-01) | Homografía para fotografías; `métrico` / `aproximado` / `no_métrico` (§8) | Sí, con la separación imagen→papel→mundo |
| "80% de líneas y símbolos" como criterio (parte 1 CU-02) | Rechazado sin clase, conjunto de prueba ni tolerancia (§9.3) | Sí |
| Hash SHA-256 "asociado a credenciales" y PDF "inmutable" (parte 1 CU-05) | El hash prueba integridad, no identidad ni custodia; manifiesto + eventos (§11) | Sí, con firma concreta |
| Hash combinado imagen + DXF (parte 2 CU-04) | Hash por artefacto (§11.1) | Sí |
| Sin no-objetivos ni antiobjetivos | §3.2 y §3.3 explícitos | Sí |
| DXF como modelo de negocio | Modelo canónico del que derivan DXF y PDF (§7) | Sí, con esquema completo |
| Dataset de 50–100 croquis "para entrenar YOLO" | Insuficiente; sirve para descubrir variabilidad (§9.3) | Sí |

---

## 3. Hallazgo principal: el croquis no es un plano a escala

### 3.1 Qué dicen los documentos

- Parte 1, CU-01: "El usuario marca dos puntos... digita el valor en metros... todas las distancias medidas digitalmente sobre el canvas corresponden con precisión a la proporción definida".
- Parte 2, CU-01 paso 3: "marca los dos extremos del ancho de la calle en la imagen e ingresa el valor real 7,50 m".
- Sketch2CAD §8.2 método 1 ("escaneo frontal: una distancia conocida y validación de escala uniforme") y CA-05 ("las distancias verificadas en el modelo, DXF y PDF coinciden").

### 3.2 Por qué es un error

Un técnico planimetrista en campo dibuja **topología y proporción aproximada**: qué hay, dónde está respecto a qué, y anota los números medidos con cinta, odómetro o distanciómetro. El trazo de 7 cm que representa la calzada no mide 7,50 m a ninguna escala; la cota "7,50" escrita al lado sí. Las posiciones de evidencias se fijan en campo con métodos documentados:

- **Triangulación:** distancia desde dos puntos fijos conocidos (esquinas, postes, hitos) hasta la evidencia.
- **Línea base + offset (coordenadas rectangulares):** distancia a lo largo de una línea de referencia (cordón, pared) y distancia perpendicular desde ella.

Si el sistema calibra píxeles y después deja medir sobre el canvas, el DXF saldrá "a escala 1:1" con una geometría inventada. La parte 2 lo intuye (CU-02 precondición: "cotas trianguladas desde dos esquinas fijas hacia cada evidencia") pero el flujo nunca usa esas cotas: la IA las "lee" y el perito "verifica a ojo".

### 3.3 Qué debe hacer la spec MVP

- Distinguir dos transformaciones: **imagen → papel** (homografía o DPI del escaneo, sirve para procedencia y para que las sugerencias caigan sobre el trazo) y **papel → mundo** (solo existe si el dibujo se hizo a escala).
- Dos **modos de fuente** declarados por el técnico: `plano_a_escala` (papel milimetrado, escala declarada, calibración geométrica válida) y `croquis_esquematico` (la geometría dibujada es una propuesta topológica; la geometría final se reconstruye a partir de cotas).
- Herramientas de edición **guiadas por cota**: fijar longitud de un segmento, fijar un punto por dos distancias (intersección de circunferencias, con elección de solución), fijar un punto por línea base + offset.
- Entidad `dimension` con semántica de **cota declarada** (la verdad) frente a **medida derivada**; indicador de discrepancia, nunca corrección silenciosa.
- Estado métrico por plano: `métrico` solo si las coordenadas provienen de cotas, triangulación o escaneo declarado a escala; `aproximado` por defecto; el PDF lo imprime.
- Tabla de indicios numerados con coordenadas locales (estaba en parte 2 y se perdió en Sketch2CAD; es de lo más valioso para el técnico).

→ Resuelto en `docs/specs/Sketch2CAD_MVP.md` §4, §6 (CU-04), §11.

---

## 4. Errores e inconsistencias técnicas puntuales

| # | Documento y sección | Problema | Corrección en MVP |
|---|---|---|---|
| 4.1 | Parte 2 CU-02 | "Tabla de evidencias georreferenciadas". No hay georreferencia; es un sistema de coordenadas local (origen, ejes, Norte como ángulo). Confundirlo es un riesgo pericial. | §4 y §11: sistema local explícito; georreferenciación fuera de alcance. |
| 4.2 | Parte 2 CU-03 | "Arrastra un punto para corregir un ángulo que no era perpendicular en el dibujo original". Ortogonalizar es una decisión interpretativa. | §6 CU-04 y §14: snapping y ortogonalización se registran como eventos explícitos con el usuario que los aplicó. |
| 4.3 | Parte 2 CU-02 | "Casquillos (.9mm / .45)": el calibre no es visible en un símbolo de croquis ni es tarea de visión; es atributo pericial ingresado por el técnico. Además ".9mm" es un error de "9 mm". | §12: los símbolos tienen atributos editables; nada se infiere. |
| 4.4 | Parte 1 §1.1 | "Estudios recientes... han demostrado que la automatización... reduce el margen de error". El único soporte en la carpeta es `docs/references/document.pdf`, una revisión bibliográfica sobre escáneres 3D, drones e IDP, sin datos empíricos sobre vectorización de croquis. El dato "20% de fallas judiciales (UNODC 2021)" viene de ese paper y no está verificado. | §1: se reformula como hipótesis de valor a medir con el MVP (tiempo de conversión, errores de transcripción). No se cita el 20%. |
| 4.5 | Parte 1, parte 2, Sketch2CAD | Tres nomenclaturas de capas incompatibles: `ESTRUCTURA/EVIDENCIAS/COTAS`, `L_CALZADA/L_VEHICULOS/L_MUROS/L_BALISTICA`, `L_STRUCTURE/...`. | §12: se adopta la de Sketch2CAD, se añaden `L_SYMBOLS` y `L_SHEET`, y se declaran obsoletas las otras dos. |
| 4.6 | Sketch2CAD §9.1 vs §3.2 | §9.1 incluye "clasificación de un catálogo reducido de símbolos" como capacidad inicial; §3.2 excluye detectar evidencias; la decisión acordada es sin detector entrenado. | §2 y §12: paleta manual de símbolos; detección automática diferida. |
| 4.7 | Sketch2CAD §12 | `CALIBRATION_PENDING` antecede a `PROCESSING`, pero detección de líneas y OCR no necesitan calibración. Faltan tabla de transiciones, permisos por transición, diferencia entre `EXPORTED` y `SEALED`, y qué pasa en `REOPENED` con un paquete ya entregado. | §9: máquina de estados con transiciones y permisos; `suggest_lines` se encola al aceptar la calibración para trabajar sobre la imagen rectificada, y `ocr_crop` no depende de ella. |
| 4.8 | Sketch2CAD §14.2 vs RF-09 | §14.2 pide "advertencia sobre elementos no validados" en el PDF; RF-09 exporta solo desde revisión validada. | §13: exportación **borrador** (marca de agua, sin manifiesto) y exportación **final** (solo desde `VALIDATED`). |
| 4.9 | Sketch2CAD §7 | `confidence: 0.0` para entidades manuales es ambiguo; `geometry` es un string sin estructura; "nunca guardar solo píxeles" contradice que las sugerencias de IA nacen en píxeles. | §8: `confidence: null` en manuales; geometría tipada; coordenadas duales `image_px` y `world_m` con `transform_id`. |
| 4.10 | Sketch2CAD §11.3 vs §17 | §11.3 exige MFA, legal hold, cifrado en reposo y backups probados; §17 los pone en Fase 3. | §2 y §15: marcados como diferidos con punto de extensión. |
| 4.11 | Sketch2CAD §6.2 | "MinIO o AWS S3" como si fueran intercambiables. MinIO es AGPL-3.0 y su edición comunitaria se está reduciendo. | §7: interfaz `Storage`, disco direccionado por contenido por defecto, S3 opcional. |
| 4.12 | Sketch2CAD §14.2 | No define cómo se elige la escala del PDF. "Ajustar a página" produce escalas no normalizadas. | §13: escalas normalizadas (1:50, 1:100, 1:200, 1:250, 1:500) con sugerencia automática. |
| 4.13 | Sketch2CAD §17 Fase 0 | Exige entrevistas, tolerancias y catálogo institucional antes de empezar. Bloquea el MVP. | §18: catálogo y tolerancias por defecto configurables; validación con 2–3 técnicos usando el prototipo. |
| 4.14 | Sketch2CAD §4 | Cuatro roles más separación de funciones. Pesado para MVP. | §3: tres roles (técnico, revisor, administrador); "custodio" es un permiso, no un rol. |
| 4.15 | Sketch2CAD §5 y RF-02 | Asume una imagen por expediente. Un hecho suele tener croquis general más detalles. | §4: expediente → N fuentes → N planos, cada plano con una fuente primaria. |
| 4.16 | Sketch2CAD §16 | "Coma decimal cuando corresponda" sin regla. | §17: OCR y entrada aceptan `3,50` y `3.50`; el modelo y el DXF usan punto. |

---

## 5. Lo que faltaba para poder construir

Sketch2CAD v1.0 es una buena spec de producto, pero no basta para que un equipo empiece. La spec MVP añade:

- **Modelo de datos** (§8): expediente, fuente, plano, revisión, entidad, calibración, job, evento, artefacto, catálogo. Revisión como snapshot JSON canónico inmutable + log de eventos; hash sobre el JSON canónico.
- **API REST** (§9): recursos, endpoints, payload de sugerencias, cómo se fusionan en la revisión de trabajo, ciclo de vida del job y polling.
- **Convención de coordenadas** (§11): imagen con Y hacia abajo, mundo con Y hacia arriba, origen local, Norte como ángulo; el modelo no se rota.
- **Pipeline de procesamiento** (§10) con parámetros y salidas concretas.
- **Reglas DXF** (§13): R2013, `$INSUNITS=6`, `LWPOLYLINE`, `INSERT` + `ATTRIB`, `DIMENSION` lineal, validación con `audit()` y relectura.
- **Reglas PDF** (§13): escala normalizada, hoja, cajetín, escala gráfica, Norte, leyenda, tabla de indicios, original como anexo, PDF reproducible.
- **Control de calidad de imagen** con umbrales (§10).
- **Concurrencia**: lease de edición por plano (§6 CU-05).
- **Registro de indicios por expediente** con numeración única compartida entre plano general y planos de detalle (§4, §8).
- **Reglas de reconstrucción por cotas** (§8.2, §11.3): sin topología compartida, estiramiento tipo CAD al fijar longitud, restricciones unidireccionales que se recalculan, y regla exacta de cuándo un plano es `metric`.
- **Qué pasa al recalibrar o mover el sistema local** (§11.1): calibración bloqueada tras la primera entidad; el sistema local se puede cambiar y aplica una transformación rígida registrada.
- **Cola de trabajos sin broker** (§7).
- **Manifiesto y firma del MVP** (§14): JSON canónico RFC 8785, SHA-256 por artefacto, log con encadenamiento de hashes, firma Ed25519 con clave del servidor.
- **Estrategia de pruebas** (§16): croquis sintéticos con geometría conocida, DXF golden, verificación de escala horizontal/vertical/diagonal, licencias en CI.
- **Hitos de implementación** (§18).

---

## 6. Herramientas: qué se mantiene, qué se reemplaza y por qué

| Tecnología en las specs | Decisión | Alternativa / razón |
|---|---|---|
| Spring Boot (backend) + FastAPI (IA) | **Reemplazar por FastAPI único** | ezdxf, OpenCV, shapely, OCR y ReportLab son Python. Dos lenguajes duplican modelos, validaciones y despliegue sin ganancia en MVP. Spring Boot vuelve a ser opción solo si una institución lo exige. |
| React / Next.js (PWA) | **Mantener React, quitar Next y PWA** | React + TypeScript + Vite. No hay SSR ni SEO detrás de un login. PWA/offline queda diferido con sus requisitos propios. |
| Three.js | **Quitar** | Alcance 2D. |
| Canvas API | **Reemplazar por SVG** | Entidades como elementos SVG con hit-testing nativo, imagen original como `<image>` de fondo, zoom/pan con `react-zoom-pan-pinch` o `d3-zoom`. Pasar a Konva/react-konva (MIT) solo si rinde mal con miles de entidades. Descartados: tldraw (licencia con marca de agua), Excalidraw (no es editor de precisión), Fabric.js (canvas sin ventaja). |
| OpenCV | **Mantener** | Homografía (`findHomography`), umbral adaptativo, morfología, varianza del Laplaciano para QC. No usar `HoughLinesP` sobre trazo manual: fragmenta. |
| Vectorización (sin definir) | **Añadir pipeline clásico** | Binarizar → `skimage.morphology.skeletonize` → grafo (`sknw` o `skan`) → polilíneas → `shapely.simplify` (Douglas-Peucker) → snapping ortogonal registrado. Produce líneas centrales, que es lo que CAD necesita. Descartados vtracer/potrace (dan contornos rellenos). M-LSD (Apache 2.0) solo si lo clásico no basta. |
| Shapely | **Mantener** | Simplificación, snapping, intersección de circunferencias para triangulación. |
| YOLO v8/v11 (Ultralytics) | **Quitar del MVP** | Licencia AGPL-3.0 (obliga a liberar el código o pagar licencia comercial) y no hay dataset. En MVP los símbolos se colocan a mano. Si más adelante se entrena: YOLOX o RT-DETR vía HF transformers (Apache 2.0), o template matching con OpenCV para el catálogo. |
| Tesseract | **Quitar** | Muy pobre en manuscrito. |
| PaddleOCR | **Quitar del MVP** | Apache 2.0 pero dependencia pesada (PaddlePaddle), instalación frágil, entrenado en impreso. |
| OCR manuscrito (sin definir) | **Añadir OCR asistido** | `microsoft/trocr-base-handwritten` (MIT, corre en CPU) sobre el recorte que el técnico marca; propone el número con confianza, el técnico confirma con un clic. docTR (Apache 2.0) como detección automática de regiones en fase 2. El OCR manuscrito libre será mediocre: la UX se diseña para que corregir cueste un clic y se mide la tasa de aceptación desde el día uno. |
| ezdxf | **Mantener** | MIT. Único generador DXF. `doc.units = units.M`, R2013, `LWPOLYLINE`, bloques con `ATTRIB`, `DIMENSION` lineales. Validar con `ezdxf.audit()` y relectura. No usar `ezdxf.addons.drawing` con PyMuPDF para el PDF (AGPL). |
| PDF técnico (sin definir) | **Añadir ReportLab** | BSD. Control exacto de puntos (1 m a 1:100 = 10 mm = 28,35 pt), `invariant=1` para PDFs reproducibles con hash estable. Alternativa WeasyPrint con unidades `mm` en CSS. |
| PDF de entrada de una página | **Añadir pypdfium2** | Apache/BSD. No PyMuPDF (AGPL). |
| PostgreSQL | **Mantener** | Geometría como JSONB, shapely para cálculo. |
| PostGIS | **Quitar** | No hay CRS ni consultas espaciales en MVP. |
| MinIO / AWS S3 | **Reemplazar en MVP** | Interfaz `Storage` con driver de disco direccionado por contenido (`sha256/ab/cd/...`, write-once). S3 con Object Lock como driver opcional para nube. MinIO descartado por AGPL; SeaweedFS (Apache 2.0) si hace falta S3 autoalojado. |
| Broker de mensajes | **Quitar** | Procrastinate (MIT): cola sobre PostgreSQL con `LISTEN/NOTIFY`. Alternativa mínima: tabla `jobs` con `FOR UPDATE SKIP LOCKED`. Nada de Celery, Redis ni RabbitMQ. |
| Autenticación (sin definir) | **Añadir** | Usuarios locales con `argon2-cffi` y sesiones con cookie `HttpOnly`. OIDC con Authlib (BSD) contra el IdP institucional en fase posterior. |
| Firma y manifiesto (sin definir) | **Añadir** | Manifiesto JSON canónico (RFC 8785) + SHA-256 por artefacto + firma Ed25519 con `cryptography` y clave del servidor. Firma PAdES del PDF con pyHanko (MIT) y sellado RFC 3161 en fase institucional. |
| Servicios externos de IA | **Fuera por defecto** | Todo corre local. Un modelo multimodal (p. ej. Claude) para proponer entidades a partir del croquis es un experimento de fase 2 detrás de un flag que exige autorización institucional explícita. Nunca por defecto. |

**Regla transversal:** lista blanca de licencias (MIT, BSD, Apache 2.0, LGPL) verificada en CI. Esto habría detectado YOLO, MinIO y PyMuPDF antes de escribirlos en la spec.

---

## 7. Riesgos principales del MVP

| # | Riesgo | Mitigación en la spec MVP |
|---|---|---|
| 1 | **Falsa precisión métrica**: el plano afirma metros que salen de píxeles de un croquis no escalado. | Modos de fuente; estado `aproximado` por defecto; `métrico` solo desde cotas, triangulación o escaneo a escala; el PDF imprime el estado. |
| 2 | **OCR manuscrito insuficiente** convierte la asistencia en ruido. | OCR asistido por recorte, confianza visible, corrección en un clic, métrica de aceptación. |
| 3 | **Licencias virales** en un producto institucional. | Lista blanca en CI; YOLO, MinIO y PyMuPDF excluidos. |
| 4 | **Confundir hash con cadena de custodia** ante un tribunal. | Manifiesto firmado + log encadenado; nota de validez en el PDF de auditoría; revisión jurídica antes de producción. |
| 5 | **Artefactos no reproducibles** rompen la verificación de hashes. | JSON canónico, ReportLab invariante, orden determinista de entidades, versiones fijadas. |
| 6 | **Datos sensibles** (escenas de homicidio) en discos de desarrollo o logs. | Dataset de prueba sintético; sin imágenes en logs; cifrado de disco en entornos reales. |
| 7 | **Vectorización ruidosa**: trazo tembloroso genera cientos de mini-polilíneas. | Simplificación agresiva, filtro de longitud mínima, aceptar/rechazar por lote. |
| 8 | **Pérdida de trabajo por edición concurrente.** | Lease de edición por plano. |
| 9 | **Decisión de stack sin cerrar** retrasa el arranque. | Python fijado en la spec. |

---

## 8. Alcance mínimo propuesto

Lo que entra (detallado en `docs/specs/Sketch2CAD_MVP.md` §2):

1. Expedientes con tipo de hecho, estado y usuarios autorizados; tres roles.
2. Ingesta de JPEG/PNG/TIFF/PDF de una página; SHA-256; almacenamiento inmutable; QC como advertencia.
3. Calibración imagen→papel: escaneo (DPI o segmento) u homografía de 4 puntos; error de reproyección; modo de fuente.
4. Sugerencia de polilíneas por esqueletización; aceptar/rechazar por lote.
5. OCR asistido de cotas por recorte.
6. Editor SVG con pan/zoom, original superpuesto, dibujo de línea/polilínea/punto/texto/cota, vértices, snapping registrado, undo/redo, medir, fijar longitud a cota.
7. Posicionamiento por triangulación y por línea base + offset; tabla de indicios.
8. Paleta de símbolos desde catálogo configurable.
9. Revisiones inmutables + eventos; validación solo por revisor; reapertura crea revisión.
10. Exportación borrador (marca de agua) y final: DXF R2013, PDF a escala normalizada con anexo del original, manifiesto firmado, PDF de auditoría, ZIP.
11. Cola sobre PostgreSQL, un worker, polling de estado.

Lo que se difiere explícitamente: detector de símbolos entrenado, OCR automático de página completa, múltiples puntos de control con ajuste por mínimos cuadrados, PostGIS, S3/MinIO, broker, MFA, OIDC, legal hold, retención automática, firma institucional y sellado de tiempo, modo offline/PWA, composición de varias imágenes en un plano, catálogo de capas con colores y tipos de línea institucionales, optimización táctil, 3D.

---

## 9. Sobre el artículo de referencia (`docs/references/document.pdf`)

Castillo, Cervantes y Montes (2025), *La Inteligencia Artificial en la disciplina de Planimetría Forense*, Semilla Científica nº 8, UMECIT. Es una revisión bibliográfica sin trabajo de campo. Trata escáneres LiDAR, drones, reconstrucción 3D e IDP; no evalúa vectorización de croquis ni OCR manuscrito. Sirve para contextualizar el problema y para recordar los riesgos éticos (privacidad, sesgo, validez legal), no para afirmar que un sistema como Sketch2CAD "reduce el margen de error". La spec MVP convierte esa afirmación en una hipótesis a medir.
