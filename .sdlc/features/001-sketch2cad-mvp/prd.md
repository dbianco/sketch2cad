# PRD: Sketch2CAD MVP

**Product Owner:** Daniel Oliva
**Engineering Manager:** Daniel Oliva (equipo de una persona)
**Client:** Internal
**Status:** Draft v0.2
**Date:** 2026-10-24
**Version:** 1.0
**Target Release:** 2027-Q1 (estimación base: 15 semanas de una persona a tiempo completo desde el inicio de H0)

> **Context:** Sketch2CAD es una aplicación web que convierte el croquis de campo de un siniestro vial, una escena del crimen o una inspección ocular en un plano 2D editable, medible y auditable, y lo exporta a DXF y PDF técnico. La geometría final se reconstruye a partir de las cotas del técnico, no de los píxeles; la IA solo propone y el perito valida. Fuente de verdad técnica: `docs/specs/Sketch2CAD_MVP.md` v2.0. Evaluación de las specs previas: `docs/evaluation/Evaluacion_especificaciones.md`.

---

## 1. Problem

### 1.1 Core Gaps

- Los técnicos planimetristas documentan el lugar de los hechos con notas de campo, bocetos a mano alzada y croquis. Pasarlos a un plano CAD estandarizado es un proceso manual.
- Un croquis de campo normalmente no está a escala. La información métrica real está en las cotas escritas y en mediciones de referencia (triangulación desde dos puntos fijos, línea base + offset). Las herramientas genéricas de calibración por píxeles producen distancias falsas con apariencia de precisión.
- No existe hoy una trazabilidad de cada decisión entre el croquis original y el plano final: qué se tomó del croquis, qué corrigió el técnico, quién validó.

### 1.2 Business Impact

- Demoras y errores de transcripción, escala u orientación en la conversión, con efecto en los tiempos de la investigación penal.
- Riesgo probatorio si un plano muestra una precisión que no se puede verificar.
- Impacto cuantitativo: estimación del dueño del proyecto, sin medición: pasar un croquis a CAD tarda unas 1,0 hora por croquis. Se verifica en H6 cronometrando la conversión manual de los croquis de validación (ver G-1). El artículo de referencia (`docs/references/document.pdf`) es una revisión bibliográfica sin datos sobre este flujo; las cifras que cita no se usan aquí.

### 1.3 Users

| Persona | Cómo le afecta |
|---|---|
| Técnico planimetrista | Rehace a mano en CAD lo que ya dibujó en campo; arrastra errores de transcripción. |
| Perito revisor | Debe verificar medidas y geometría sin poder rastrear su origen. |
| Custodio o responsable del expediente | Necesita saber qué se entregó y que no cambió. |
| Administrador | Gestiona usuarios y catálogos sin herramienta específica. |

### 1.4 Scope

Este PRD cubre el MVP completo (hitos H0 a H6 del plan de la spec). Las capacidades diferidas a fases 2 y 3 están en Non-Goals y en REQ de prioridad P2. La feature `h0-foundation` del orquestador SDD es la primera entrega.

---

## 2. Business Case

> Sección obligatoria. El producto es interno y no genera ingresos en esta iteración. El caso de negocio se plantea como ahorro de horas-persona y reducción de riesgo probatorio. Solo se usan cifras del plan o declaradas por el dueño del proyecto; los demás parámetros se miden en H6.

### 2.1 Revenue Opportunity

No aplica: producto interno, sin ingresos en esta iteración. El valor es ahorro de tiempo, calculado así:

`Horas ahorradas por año = (t_manual − t_herramienta) × croquis por año`

| Parámetro | Valor | Origen |
|---|---|---|
| t_manual | 1,0 h por croquis | Estimación del dueño del proyecto, sin medición |
| t_herramienta | Se mide en H6 | Cronometraje de los croquis de validación (REQ-20) |
| Croquis por año | Se mide al tener un usuario real | Depende de DEC-1 |
| Esfuerzo de construcción | 600 h (15 semanas × 40 h) | Plan de la sección 11 |

Punto de equilibrio: `600 h / (1,0 h − t_herramienta)` croquis. Con t_herramienta igual a 0,5 h el equilibrio llega a los 1200 croquis; el cálculo se rehace con el valor medido en H6.

### 2.2 Customer Pipeline

| Customer | Stage | Estimated Deal Impact | Notes |
|---|---|---|---|
| Ninguno identificado | No aplica | No aplica | Producto interno (Client = Internal, DEC-3). Si aparece una institución piloto, se agrega aquí. |

### 2.3 New Market / Segment Unlock

No aplica en esta iteración. Segmento candidato para fases posteriores: unidades de planimetría y criminalística de policías y fiscalías de habla hispana. No se cuantifica porque no hay datos.

### 2.4 Cost of Inaction

Sin el producto, cada croquis sigue costando la hora de redibujo manual que estima el dueño del proyecto (1,0 h por croquis, ver 2.1), con riesgo de errores de transcripción de cotas y de planos que muestran precisión no verificable. El costo total se calcula como `1,0 h × croquis por año` cuando se conozca el volumen (DEC-1).

---

## 3. Goals

| # | Goal | Type | Target | OKR Reference |
|---|---|---|---|---|
| G-1 | Reducir el tiempo de pasar un croquis a plano exportable frente a dibujarlo en un CAD libre (FreeCAD) | User | Medir y reportar la línea base (la estimación de partida es 1,0 h por croquis) sobre ≥ 10 croquis; el umbral de mejora se fija para v2 con ese dato (DEC-4) | — |
| G-2 | Eliminar errores de transcripción de cotas | User | ≥ 99 % de las cotas declaradas exportadas coinciden con el valor escrito en el croquis | — |
| G-3 | Que las sugerencias de IA ahorren trabajo | User | Medir y reportar el porcentaje de polilíneas aceptadas sin cambios y el tiempo de edición con y sin sugerencias, sobre ≥ 10 croquis; el umbral se fija para v2 con ese dato (DEC-4) | — |
| G-4 | Preservar las distancias del modelo en DXF y PDF | Technical | Tres distancias de control (horizontal, vertical, diagonal) coinciden en modelo, DXF y PDF dentro de 0,5 % | — |
| G-5 | Garantizar la integridad verificable del paquete final | Technical | 100 % de alteraciones de un byte en artefactos o eventos detectadas por el verificador, con el artefacto afectado nombrado | — |
| G-6 | Construir el MVP con una sola persona dentro del plan | Business | 15 semanas, siete hitos (H0 a H6) | — |
| G-7 | Mantener una base libre de licencias virales | Technical | 0 dependencias fuera de la lista blanca en CI | — |

---

## 4. Non-Goals

| # | Non-Goal | Rationale |
|---|---|---|
| NG-1 | Reconstrucción 3D, nubes de puntos, drones, LiDAR | Alcance exclusivamente 2D. |
| NG-2 | Identificación automática de cadáveres, sangre, armas, casquillos o impactos | Requiere validación científica y jurídica independiente. |
| NG-3 | Trayectorias balísticas y conclusiones sobre la mecánica del hecho | Es interpretación pericial, no documentación. |
| NG-4 | Georreferenciación e integración con GIS | El sistema de coordenadas es local; PostGIS queda fuera. |
| NG-5 | Generación autónoma de una pericia con validez judicial | El sistema asiste; el perito decide y valida. |
| NG-6 | Detector de símbolos entrenado | Sin dataset autorizado; los símbolos se colocan a mano desde un catálogo. Fase 2. |
| NG-7 | Entrenamiento con datos forenses sin autorización y gobierno de datos | Riesgo legal y de privacidad. |
| NG-8 | MFA, OIDC, legal hold, retención automática, firma institucional (PAdES, RFC 3161) | Operación institucional. Fase 3, con puntos de extensión en el MVP. |
| NG-9 | Modo offline, PWA y optimización táctil | Requiere cifrado local, sincronización y borrado seguro. Fase 3. |
| NG-10 | Servicios externos de IA | Ningún dato de expediente sale del despliegue; configuración deshabilitada por defecto. |
| NG-11 | Componer varias fuentes en un plano | Cada plano tiene una fuente primaria; fase 3. |
| NG-12 | Interfaz de administración de usuarios y catálogo | Con una persona, queda en API y datos semilla. |

---

## 5. User Stories

**Persona: Técnico planimetrista**
> Levanta la escena en campo con cinta o distanciómetro, dibuja un croquis a mano y anota las cotas. Después debe producir el plano. Trabaja en PC o tablet.

**Persona: Perito revisor**
> Verifica que el plano refleje las cotas declaradas, valida y autoriza el paquete final.

**Persona: Administrador**
> Da de alta usuarios y mantiene el catálogo y la configuración.

**Persona: Auditor externo**
> Recibe el paquete final y debe poder comprobar su integridad sin acceso al servidor.

### Expedientes y fuentes

**US-01 — Crear expediente.** As a técnico, I want to crear un expediente con tipo de hecho, fecha y ubicación declarada so that agrupo fuentes, planos y eventos de un caso.
- Given que inicié sesión, When creo un expediente, Then se asigna un código único y legible y solo yo y los usuarios autorizados lo vemos.

**US-02 — Cargar el croquis.** As a técnico, I want to subir una foto o escaneo del croquis so that conservo el original intacto.
- Given un archivo JPEG, PNG, TIFF o PDF de una página, When lo subo, Then el sistema calcula SHA-256, guarda el original inmutable y muestra el resultado del control de calidad.
- Given que descargo el original después, When comparo su hash, Then coincide con el registrado.

### Calibración y coordenadas

**US-03 — Calibrar la imagen.** As a técnico, I want to calibrar la imagen por escaneo o por 4 puntos de una fotografía so that las sugerencias y el fondo queden sobre el trazo.
- Given una fotografía oblicua de un patrón conocido, When marco las 4 esquinas, Then veo la imagen rectificada y el error de reproyección y puedo aceptar o repetir.
- Given una fotografía sin rectificar, When intento marcarla como métrica, Then el sistema lo impide.

**US-04 — Declarar el modo de fuente.** As a técnico, I want to indicar si el dibujo está a escala o es esquemático so that el sistema sepa si la geometría proviene del trazo o de las cotas.
- Given un plano nuevo, When no declaro el modo, Then el valor por defecto es croquis esquemático.

**US-05 — Definir el sistema local.** As a técnico, I want to fijar origen, dirección +X y Norte so that las coordenadas del plano sean mías y no del archivo.
- Given entidades ya creadas, When cambio origen o eje X, Then todas se transforman rígidamente y queda un único evento.

### Edición y reconstrucción por cotas

**US-06 — Editar el plano.** As a técnico, I want to dibujar y modificar líneas, polilíneas, puntos, textos, cotas y símbolos, con deshacer y rehacer, so that corrijo lo que la IA propuso o dibujo desde cero.
- Given un vértice, When lo arrastro, Then la geometría cambia y el evento queda registrado con valor anterior y nuevo.
- Given una acción de snapping u ortogonalización, When se aplica, Then se registra como evento explícito.

**US-07 — Fijar longitud a una cota.** As a técnico, I want to indicar que un segmento mide 4,20 m so that el plano respete la medición de campo.
- Given un segmento y un vértice ancla, When fijo la longitud, Then el segmento y los vértices del mismo lado se estiran y la cota queda declarada.

**US-08 — Ubicar un indicio por triangulación.** As a técnico, I want to fijar un punto por sus distancias a dos esquinas so that la posición salga de las cotas.
- Given referencias métricas A y B y distancias dA y dB, When confirmo, Then el sistema calcula las dos intersecciones y preselecciona la más cercana a lo dibujado.
- Given distancias incompatibles, When intento calcular, Then no se mueve nada y se explica la causa.

**US-09 — Ubicar un indicio por línea base y offset.** As a técnico, I want to fijar un punto por distancia a lo largo de una línea de referencia y distancia perpendicular so that documento una vía con coordenadas rectangulares.
- Given una línea base métrica, When ingreso distancia, offset y lado, Then el punto queda fijado y guarda su restricción.

**US-10 — Tabla de indicios.** As a técnico, I want to numerar los indicios una sola vez por expediente so that el indicio 7 sea el mismo en el plano general y en el de detalle.

**US-11 — Ver el estado métrico.** As a técnico, I want to saber si el plano es métrico, aproximado o no métrico y qué entidades lo impiden so that no entregue un plano con precisión aparente.

### Sugerencias de IA

**US-12 — Revisar polilíneas sugeridas.** As a técnico, I want to aceptar o rechazar por lote las polilíneas que propone el sistema so that ahorro trazar a mano.
- Given sugerencias en estado `suggested`, When acepto o rechazo, Then cambia su estado y queda el historial; las de baja confianza aparecen resaltadas.

**US-13 — OCR asistido de cotas.** As a técnico, I want to marcar un recorte con un número manuscrito y recibir una propuesta so that corrijo con un clic.
- Given un recorte, When el OCR devuelve valor y confianza, Then acepto o escribo el valor y se asocia al segmento.

### Validación, exportación y auditoría

**US-14 — Validar y bloquear.** As a perito revisor, I want to revisar y validar una revisión para congelarla so that no cambie sin dejar rastro.
- Given un plano `VALIDATED`, When intento editarlo, Then no puedo sin reabrir; reabrir crea una revisión nueva y conserva la anterior.

**US-15 — Exportar borrador.** As a técnico, I want to exportar DXF y PDF con marca de agua `DRAFT` so that reviso en CAD antes de validar.

**US-16 — Exportar el paquete final.** As a perito revisor, I want to generar DXF, PDF técnico, reporte de auditoría, manifiesto firmado y ZIP desde la revisión validada so that entrego el expediente completo.
- Given una revisión validada, When exporto, Then el DXF pasa `audit()`, abre en FreeCAD, LibreCAD y QCAD, y el PDF imprime la escala normalizada correcta.

**US-17 — Verificar el paquete.** As a auditor externo, I want to verificar hashes, cadena de eventos y firma sin acceso al servidor so that confirmo que nada cambió.

**US-18 — Entender los fallos.** As a técnico, I want to ver una causa y una acción cuando algo falla so that nunca reciba un resultado aparentemente confiable por defecto.

### Administración

**US-19 — Gestionar usuarios y permisos.** As a administrador, I want to dar de alta usuarios con rol y autorizarlos por expediente so that cada quien vea solo lo suyo.
- Given un usuario no autorizado, When intenta leer, editar, exportar o descargar, Then se rechaza y el intento queda registrado.

#### User Story Traceability

| Story | Description | Requirement(s) |
|---|---|---|
| US-01 | Crear expediente | REQ-01 |
| US-02 | Cargar croquis | REQ-02 |
| US-03 | Calibrar imagen | REQ-03 |
| US-04 | Modo de fuente | REQ-03 |
| US-05 | Sistema local | REQ-04 |
| US-06 | Editar plano | REQ-05 |
| US-07 | Fijar longitud | REQ-06 |
| US-08 | Triangulación | REQ-06 |
| US-09 | Base + offset | REQ-06 |
| US-10 | Tabla de indicios | REQ-08 |
| US-11 | Estado métrico | REQ-07 |
| US-12 | Polilíneas sugeridas | REQ-17 |
| US-13 | OCR asistido | REQ-18 |
| US-14 | Validar y bloquear | REQ-10 |
| US-15 | Exportar borrador | REQ-12, REQ-13 |
| US-16 | Paquete final | REQ-12, REQ-13, REQ-14 |
| US-17 | Verificar paquete | REQ-11, REQ-14 |
| US-18 | Fallos explícitos | REQ-15 |
| US-19 | Usuarios y permisos | REQ-01 |

---

## 6. Prototype / Design Reference

No hay Figma. La referencia de diseño es un prototipo HTML de seis pantallas, versionado en `docs/prototype/` (el texto de la spec y del PRD manda si hay contradicción). Copia en línea, privada: https://claude.ai/artifact/QQiw5ef4cdATbbfWV3MbNi. La referencia de comportamiento del editor es la sección 6 (CU-04) y la sección 11.3 de `docs/specs/Sketch2CAD_MVP.md`. El diseño visual definitivo del editor se ajusta en H2, con el resultado del spike 1.

| Board | Screen name/description |
|---|---|
| `Main.dc.html` | 1 · Lista de expedientes |
| `Upload.dc.html` | 2 · Carga de fuente y control de calidad |
| `Calibration.dc.html` | 3 · Calibración (escaneo, 4 puntos) |
| `Editor.dc.html` | 4 · Editor 2D con fuente superpuesta, paleta y herramientas métricas |
| `Review.dc.html` | 5 · Revisión y validación |
| `Export.dc.html` | 6 · Exportación y descarga del paquete |

---

## 7. Validation Rules ⚡

Aplica: la funcionalidad recibe archivos, números de cota y coordenadas.

| Rule | Category | Description | Error Message |
|---|---|---|---|
| V-01 | Format | Tipos permitidos: JPEG, PNG, TIFF y PDF de una sola página, validados por contenido (magic bytes), no por extensión. | "El archivo no es un JPEG, PNG, TIFF o PDF de una página válido." |
| V-02 | Structure | Tamaño máximo de subida: 50 MB. | "El archivo supera los 50 MB permitidos." |
| V-03 | Structure | El hash calculado en el servidor debe coincidir con el calculado en el cliente. | "El archivo se alteró durante la subida. Vuelve a cargarlo." |
| V-04 | Format | Las cotas aceptan coma o punto decimal; el modelo guarda punto. Solo valores numéricos mayores que cero. | "Ingresa una distancia numérica mayor que cero, por ejemplo 3,50." |
| V-05 | Business | La calibración por 4 puntos exige un error de reproyección máximo de 1,0 mm (configurable) para aceptarse como métrica. | "El error de calibración es de {x} mm y supera el máximo de {y} mm. Repite los puntos o continúa como aproximado." |
| V-06 | Business | La triangulación requiere que las dos circunferencias se intersecten. | "Estas dos distancias no se cruzan. Revisa las medidas o las referencias elegidas." |
| V-07 | Business | Las referencias de triangulación y base+offset deben ser métricas y no depender de la entidad que se fija. | "Esta referencia no es métrica o depende del punto que intentas fijar." |
| V-08 | Business | La calibración no puede cambiarse cuando existe una entidad aceptada o manual. | "No puedes recalibrar un plano con entidades. Descártalas o crea otro plano." |
| V-09 | Business | La transición a `VALIDATED` y la exportación final requieren rol de revisor y motivo cuando se rechaza. | "Solo un revisor puede validar este plano." / "Indica el motivo del rechazo." |
| V-10 | Business | Un plano `VALIDATED` no se edita sin reabrir. | "Este plano está validado. Reábrelo para editarlo." |
| V-11 | Business | La exportación final solo desde una revisión validada. | "La exportación final requiere un plano validado." |
| V-12 | Business | Un solo editor por plano mediante lease de 10 minutos renovable. | "{usuario} está editando este plano. Entra en solo lectura o espera a que termine." |

---

## 8. Requirements

### P0 — Must Have (MVP)

**REQ-01 — Expedientes, usuarios y permisos.**
Expedientes con código único, tipo de hecho, fecha, ubicación declarada y miembros. Tres roles (técnico, revisor, administrador) y permiso `case_file:deliver`. Autorización por expediente.
- [ ] Un usuario sin autorización no puede leer, editar, exportar ni descargar un expediente ajeno.
- [ ] Cada intento denegado queda en el log (`access.denied`).
- [ ] Contraseñas con Argon2id y bloqueo temporal tras 10 intentos fallidos.

**REQ-02 — Ingesta inmutable y control de calidad.**
Carga de JPEG, PNG, TIFF y PDF de una página; SHA-256 en servidor y comparación con el del cliente; almacenamiento direccionado por contenido, write-once; QC con semáforo.
- [ ] El original descargado tiene exactamente el hash registrado.
- [ ] El QC marca como rojo imágenes con lado corto menor que 900 px, varianza del Laplaciano menor que 40 o bajo contraste, y como ámbar los umbrales intermedios.
- [ ] Las advertencias no bloquean el avance.

**REQ-03 — Calibración imagen a papel y modo de fuente.**
Métodos `scan_dpi`, `scan_segment`, `photo_homography_4pt` y `none`. Modo `scaled_drawing` o `schematic_sketch`.
- [ ] Una fotografía oblicua de un patrón conocido queda rectificada con error de reproyección dentro de 1 mm.
- [ ] Una fotografía sin rectificar no puede marcarse como métrica.
- [ ] Se guardan los puntos, la matriz y el error (máximo y RMS).

**REQ-04 — Sistema de coordenadas local.**
Origen, eje +X y Norte como ángulo; mundo con Y hacia arriba.
- [ ] Cambiar origen o eje X aplica una transformación rígida a todo el borrador en una transacción con un solo evento.
- [ ] Cambiar el Norte solo cambia el metadato y el símbolo.

**REQ-05 — Editor 2D en el navegador.**
SVG con pan y zoom, fuente superpuesta con opacidad, herramientas de dibujo, mover vértices, snapping registrado, deshacer y rehacer, medir, lease de edición.
- [ ] Todas las herramientas de la sección 6 CU-04 de la spec funcionan sobre un plano de ejemplo en un test E2E.
- [ ] Deshacer restaura la geometría exacta.
- [ ] El editor mantiene 60 fps con 2000 entidades; si no, se cambia a Konva.
- [ ] Los elementos interactivos son accesibles por teclado y están etiquetados (WCAG 2.1 AA).

**REQ-06 — Reconstrucción por cotas.**
Fijar longitud, triangulación, base + offset, cota declarada con indicador de discrepancia.
- [ ] Las coordenadas por triangulación y base+offset coinciden con las esperadas dentro de 1 cm en casos resueltos a mano.
- [ ] Distancias incompatibles no mueven nada y explican la causa.
- [ ] Una cota declarada que difiere de la medida más allá de la tolerancia se marca; el sistema nunca corrige uno de los dos valores por su cuenta.
- [ ] Las restricciones se recalculan cuando una referencia se mueve y generan evento `entity.recomputed`.

**REQ-07 — Estado métrico del plano.**
`metric`, `approximate` o `non_metric`, recalculado en cada cambio, con listado de entidades que lo impiden.
- [ ] Un plano con cualquier entidad estructural `trace_only` o `none` no puede ser `metric`.
- [ ] El estado se imprime en DXF y PDF.

**REQ-08 — Registro de indicios.**
Numeración única por expediente; cada plano referencia el indicio con un símbolo o punto; tabla de indicios con coordenadas locales.
- [ ] El mismo indicio aparece con el mismo número en dos planos del expediente.

**REQ-09 — Catálogo de capas y símbolos.**
Diez capas fijas y catálogo por defecto de símbolos (SVG + bloque DXF + atributos).
- [ ] Ninguna capa se crea al exportar.
- [ ] El catálogo por defecto no incluye símbolos de víctimas, sangre, armas ni casquillos.

**REQ-10 — Revisiones y estados.**
Revisión como snapshot JSON canónico (RFC 8785) con hash; máquina de estados con permisos por transición.
- [ ] Las transiciones no permitidas se rechazan.
- [ ] `VALIDATED` congela la revisión; reabrir crea una nueva con `parent_revision_id`.
- [ ] La revisión validada sigue recuperable byte a byte.

**REQ-11 — Auditoría con eventos encadenados.**
Cada acción de dominio genera un evento con `prev_hash` y `hash`, en la misma transacción que el cambio.
- [ ] Modificar un evento rompe la cadena y la verificación lo detecta.
- [ ] No hay imágenes ni geometría en logs de aplicación, solo identificadores.

**REQ-12 — Exportación DXF.**
ezdxf, R2013, unidades en metros, capas del catálogo, `LWPOLYLINE`, bloques con atributos, cotas.
- [ ] `audit()` sin errores y relectura igual al snapshot (tolerancia 1e-6 m).
- [ ] Abre sin errores en FreeCAD (verificación manual local) y en LibreCAD y QCAD (automatizada en CI donde sea viable).
- [ ] Dos exportaciones de la misma revisión producen el mismo hash.

**REQ-13 — Exportación PDF técnico.**
ReportLab, A4 o A3, escalas normalizadas, cajetín, escala gráfica, Norte, leyenda, tabla de indicios, anexo del original, PDF reproducible.
- [ ] Una línea de 1 m a 1:100 mide 10 mm en el PDF.
- [ ] Tres distancias de control coinciden en modelo, DXF y PDF dentro de 0,5 %.
- [ ] El borrador lleva marca de agua y no incluye tabla de hashes.

**REQ-14 — Paquete final y verificación.**
Manifiesto JSON canónico con hashes por artefacto, versiones, calibración y aprobador; firma Ed25519 con clave del servidor; ZIP; reporte de auditoría PDF; verificador independiente del servidor.
- [ ] Alterar un byte de cualquier artefacto o evento hace fallar la verificación nombrando el artefacto.
- [ ] La descarga muestra el contenido y los hashes antes de confirmar, y queda auditada.
- [ ] El reporte de auditoría declara que la firma prueba integridad y origen técnico, no autoría pericial.
- [ ] El reporte y el PDF final llevan la leyenda "Borrador técnico, no apto como prueba hasta contar con revisión jurídica", configurable y activa por defecto hasta resolver DEC-8.

**REQ-15 — Fallos explícitos.**
- [ ] Imagen ilegible, calibración fuera de tolerancia y job fallido muestran estado visible con causa y acción.
- [ ] Ningún camino produce un resultado aparentemente confiable por defecto.

**REQ-16 — Fundación técnica (H0).**
Repositorio, docker-compose con `db`, `api`, `worker`, `web`; esqueleto FastAPI y React/Vite/TypeScript; migraciones Alembic con vuelta atrás probada; CI; chequeo de licencias; dos spikes.
- [ ] Login, expediente vacío y CI verde.
- [ ] Cada migración tiene su vuelta atrás probada en CI.
- [ ] CI falla ante una dependencia fuera de la lista blanca y ante un hallazgo de severidad alta sin justificar.
- [ ] Los secretos vienen del entorno, nunca del código.
- [ ] Decisión del dueño (2026-10-24): el editor se construye en SVG. Spike 1 pasa a ser una medición de 60 fps con 2000 entidades en H0; si falla, se migra a Konva (REQ-05).
- [ ] Spike 2: se determina si ezdxf produce DXF reproducibles byte a byte (handles, `$TDCREATE`, GUIDs) y se documenta cómo fijarlos.

### P1 — Should Have

**REQ-17 — Sugerencia de polilíneas.**
`suggest_lines` por esqueletización y simplificación; aceptación y rechazo por lote; resaltado por confianza. Es el primer elemento que se recorta si el tiempo se acorta.
- [ ] Sobre croquis sintéticos, ≥ 90 % de segmentos de más de 20 mm de papel se recuperan con desviación menor que 2 mm de papel.
- [ ] Cero polilíneas espurias de menos de 5 mm tras el filtro.
- [ ] Procesar 4000 × 3000 px tarda menos de 30 s en CPU de 4 núcleos.

**REQ-18 — OCR asistido de cotas.**
TrOCR sobre recortes marcados por el técnico.
- [ ] Devuelve valor y confianza en menos de 3 s.
- [ ] Acepta coma y punto decimal y descarta sufijos de unidad.
- [ ] Se registra la tasa de aceptación.

**REQ-19 — Parámetros configurables.**
- [ ] Tolerancias, umbrales de QC, parámetros de `suggest_lines` y umbral de OCR se leen de configuración, con sobreescritura por tipo de hecho.

**REQ-20 — Validación interna (H6).**
- [ ] Existe un conjunto de ≥ 10 croquis dibujados a mano, un patrón impreso fotografiado y un conjunto sintético generado por script con geometría conocida.
- [ ] El informe mide G-1, G-2 y G-3 y declara que la medición es interna y que la validación con técnicos sigue pendiente.

### P2 — Future Consideration

**REQ-21 — Detector de símbolos entrenado** (YOLOX o RT-DETR, con dataset autorizado y anotado).
**REQ-22 — Control múltiple de calibración** con ajuste por mínimos cuadrados.
**REQ-23 — Operación institucional:** OIDC, MFA, legal hold, retención, firma PAdES y sellado RFC 3161, driver S3 con Object Lock.
**REQ-24 — Modo offline, PWA y optimización táctil.**

---

## 9. Success Metrics

Sin técnicos disponibles por ahora, todas las mediciones del MVP son internas y se reportan como tales. Los plazos "post-lanzamiento" se cuentan desde el cierre de H6.

### Leading Indicators

| Metric | Target | Measurement |
|---|---|---|
| Pruebas de aceptación CA-01 a CA-13 de la spec | 13 de 13 en verde | CI en el repositorio, a 1 semana del cierre de H6 |
| Coincidencia de distancias modelo, DXF y PDF | ≤ 0,5 % de diferencia en las tres distancias de control | Test automatizado que extrae vectores de DXF y PDF |
| Detección de alteraciones por el verificador | 100 % de los casos probados | Test de paquete alterado |
| Dependencias fuera de la lista blanca | 0 | `pip-licenses` y `license-checker` en CI |
| Cumplimiento del plan | Cierre de cada hito dentro de ±1 semana de lo planificado | Registro de hitos en el orquestador SDD |

### Lagging Indicators

| Metric | Target | Measurement |
|---|---|---|
| G-1 Tiempo frente a CAD libre | Línea base medida y reportada sobre ≥ 10 croquis (estimación de partida: 1,0 h por croquis) | Cronometraje interno del mismo plano en el MVP y en FreeCAD, a 60 días |
| G-2 Coincidencia de cotas | ≥ 99 % de cotas declaradas exportadas con el valor escrito | Revisión por una segunda persona sobre los 10 croquis, a 60 días |
| G-3 Aceptación de sugerencias | Porcentaje de polilíneas aceptadas sin cambios, medido y reportado | Eventos `entity.reviewed` sobre los 10 croquis, a 60 días |
| Observación con técnicos reales | ≥ 2 técnicos y ≥ 5 planos reales o anonimizados | Sesiones de observación, a 90 días (depende de DEC-1; fuera del alcance del MVP) |

---

## 10. Decisions

Todas las decisiones pendientes tienen resolución del dueño del proyecto (2026-10-24). Ninguna queda abierta.

| # | Question | Owner | Blocking? | Resolution |
|---|---|---|---|---|
| DEC-1 | ¿Cuándo y cómo se conseguirán técnicos planimetristas y croquis manuscritos reales? | Product | No (resuelta) | Validación interna en H6 con ≥ 10 croquis propios dibujados a mano, complementada con un conjunto sintético generado por script. Los técnicos reales quedan como observación posterior. |
| DEC-2 | ¿Quién es el Product Owner y el Engineering Manager, y cuál es el trimestre objetivo? | Product | No (resuelta) | Daniel Oliva en ambos roles; trimestre objetivo 2027-Q1. |
| DEC-3 | ¿Existe cliente, institución o piloto identificado? | Product | No (resuelta) | No. Client = Internal; el Business Case se plantea como ahorro de horas-persona. |
| DEC-4 | ¿Se confirman los objetivos G-1 y G-3? | Product | No (resuelta) | No se fijan umbrales en v1: se mide y se reporta la línea base en H6, y el umbral se define para v2 con ese dato. |
| DEC-5 | ¿Las tolerancias por defecto (1 mm de reproyección, 1 % de escala, 0,02 m de discrepancia, 0,5 % de control) sirven? | Product | No (resuelta) | Se mantienen los valores de la spec, configurables (REQ-19). |
| DEC-6 | ¿Qué lista blanca de licencias se acepta? | Engineering | No (resuelta) | MIT, BSD, Apache-2.0, PSF y LGPL, verificada en CI, con una excepción documentada por cada dependencia LGPL (por ejemplo `psycopg`) frente a la norma de la empresa de MIT o Apache-2.0. |
| DEC-7 | ¿Qué visores CAD se usan para verificar el DXF? | Product | No (resuelta) | FreeCAD, que es la herramienta disponible localmente, como verificación manual principal; LibreCAD y QCAD en CI donde sea viable. AutoCAD se agrega cuando una institución lo pida. |
| DEC-8 | ¿Qué marco legal aplica a la firma, la cadena de custodia y la retención? | Product | No (resuelta) | Ed25519 de servidor y campos sin lógica de retención, con revisión jurídica antes de producción. Hasta entonces las salidas llevan la leyenda "Borrador técnico, no apto como prueba" (REQ-14). |
| DEC-9 | ¿Dónde se aloja el despliegue y cómo se gestionan la clave de firma y los respaldos? | Engineering | No (resuelta) | docker-compose en un host con cifrado de disco; la clave de firma en un archivo con permisos 600 fuera del repositorio. KMS queda como punto de extensión. |
| DEC-10 | ¿Qué herramienta de CI se usa y qué exige el orquestador SDD para `record_commit`? | Engineering | No (resuelta) | `git init` local, repositorio remoto en GitHub y GitHub Actions como CI. La creación del repositorio es la primera tarea de H0. |

---

## 11. Timeline Considerations

### Hard Deadlines

Ninguno. Los plazos son los hitos H0 a H6 del plan, dentro de 2027-Q1.

### Proposed Phasing

| Phase | Scope | Duration | Dependencies |
|---|---|---|---|
| H0: Fundación | Repositorio, docker-compose, FastAPI + React, auth local, CI, licencias, dos spikes | 1 semana | Ninguna (DEC-6 y DEC-10 resueltas) |
| H1: Expedientes e ingesta | Expedientes, ingesta con hash y QC, almacenamiento por contenido, eventos encadenados, visor de fuente | 2 semanas | H0 |
| H2: Calibración y editor | Tres métodos de calibración, sistema local, editor SVG, lease, deshacer y rehacer, catálogo, revisiones | 3 semanas | H1; resultado del spike 1 |
| H3: Herramientas métricas | Fijar longitud, triangulación, base + offset, cotas con discrepancia, tabla de indicios, estado métrico | 2 semanas | H2 |
| H4: Exportación y auditoría | DXF y PDF (borrador y final), manifiesto, firma, ZIP, reporte, verificador | 3 semanas | H3; resultado del spike 2 |
| H5: Asistencia de IA | `suggest_lines`, OCR asistido, aceptación por lote, resaltado por confianza | 2 semanas | H2 |
| H6: Endurecimiento y validación interna | Pruebas con ≥ 10 croquis propios, medición de G-1 a G-3, ajustes, informe | 2 semanas | H4; H5 solo para CA-04, CA-05 y G-3 (si H5 se recorta, esos criterios quedan como validación posterior) |

Total: 15 semanas, una persona a tiempo completo. Cada hito se gestiona como una feature del orquestador SDD (la primera es `h0-foundation`). Si el tiempo se acorta, se recorta H5 y el producto sigue siendo útil.

### Key Dependencies

- Crear el repositorio git, el remoto en GitHub y GitHub Actions (DEC-10) y el chequeo de licencias con la lista blanca de DEC-6, ambos al inicio de H0.
- Disponibilidad de técnicos y croquis reales (DEC-1), que condiciona la validez de las métricas de G-1 a G-3.
- Resultados de los spikes de H0 para fijar la tecnología del editor y la reproducibilidad del DXF.
- Revisión jurídica de firma y cadena de custodia antes de cualquier uso en producción (DEC-8).
