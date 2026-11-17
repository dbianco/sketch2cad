# Sketch2CAD

## Especificación consolidada del producto

**Versión:** 1.0

**Estado:** Base para discovery técnico, prototipo y validación con usuarios.

**Dominio:** Documentación inicial de lugares de los hechos, siniestros viales e inspecciones oculares.

## 1. Propósito

Sketch2CAD permite transformar un croquis, boceto o nota gráfica de campo en un plano 2D estructurado y editable, asistiendo al técnico planimetrista con detección de geometría, lectura de textos y sugerencias de símbolos.

El sistema no reemplaza el levantamiento técnico, la interpretación pericial ni la aprobación del profesional responsable. La IA propone; el perito decide, corrige y valida.

## 2. Problema

La conversión manual de croquis a CAD consume tiempo y puede introducir errores de transcripción, escala, orientación o nomenclatura. El producto debe reducir ese trabajo repetitivo manteniendo:

- la imagen original intacta;
- la trazabilidad de cada transformación;
- la medición en unidades reales;
- la revisión humana obligatoria;
- la posibilidad de reproducir y auditar cada exportación.

## 3. Objetivos

### 3.1 Objetivos del MVP

1. Cargar y conservar de forma inmutable la imagen original del croquis.
2. Calibrar un plano 2D con una o más referencias métricas.
3. Corregir perspectiva cuando la fuente sea una fotografía de un plano aproximadamente plano.
4. Proponer líneas estructurales, textos y símbolos simples.
5. Permitir edición manual de todas las entidades propuestas.
6. Registrar quién, cuándo y cómo modificó cada entidad.
7. Exportar el mismo modelo validado a DXF y PDF técnico.
8. Crear un paquete final con manifiesto, hashes, versiones y auditoría.

### 3.2 No objetivos del MVP

Quedan fuera del primer producto:

- reconstrucción 3D;
- procesamiento de nubes de puntos, drones o LiDAR;
- identificación automática de cadáveres, manchas de sangre, armas, casquillos o impactos;
- reconstrucción o interpretación de trayectorias balísticas;
- conclusiones sobre la mecánica del hecho;
- georreferenciación avanzada o integración con GIS institucional;
- generación autónoma de una pericia con validez judicial;
- entrenamiento de modelos con datos forenses sin autorización y gobierno de datos.

Estas capacidades podrán evaluarse en proyectos posteriores, con validación científica y jurídica independiente.

### 3.3 Antiobjetivos

El sistema falla como producto si:

- presenta una inferencia de IA como hecho confirmado;
- altera o reemplaza el original sin conservarlo;
- exporta un plano con escala aparente pero no verificable;
- oculta incertidumbre o errores de reconocimiento;
- permite cerrar un expediente sin validación humana;
- confunde un hash con una cadena de custodia completa.

## 4. Usuarios y responsabilidades

| Rol | Responsabilidades principales |
|---|---|
| Técnico planimetrista | Carga fuentes, calibra, revisa y edita el plano |
| Perito revisor | Verifica mediciones, geometría, simbología y exportación |
| Custodio o responsable del expediente | Autoriza cierre, acceso y entrega del paquete |
| Administrador | Gestiona usuarios, permisos, catálogos y retención |
| Sistema | Conserva originales, ejecuta procesos, registra eventos y genera artefactos |

La aprobación y la administración no deben recaer obligatoriamente en la misma persona cuando la normativa institucional requiera separación de funciones.

## 5. Flujo principal

1. El usuario crea o selecciona un expediente.
2. Carga una fotografía o escaneo del croquis.
3. El sistema calcula y registra el hash del archivo original antes de procesarlo.
4. El sistema ejecuta controles de calidad: resolución, orientación, desenfoque, contraste y legibilidad.
5. El usuario selecciona el método de calibración:
   - escaneo frontal ya métrico;
   - rectificación por cuatro puntos de un plano fotografiado;
   - coordenadas locales con varios puntos de control;
   - modo no métrico, únicamente para trazado visual.
6. El usuario registra unidades, origen, orientación Norte y referencias métricas.
7. Un worker procesa la imagen de forma asíncrona y devuelve sugerencias.
8. El editor muestra la fuente original y el modelo vectorial superpuesto.
9. El usuario acepta, corrige, elimina o agrega entidades.
10. El revisor valida el plano y lo bloquea para exportación.
11. El sistema genera DXF, PDF, manifiesto y reporte de auditoría.
12. El custodio autoriza la entrega o descarga del paquete final.

Si la calidad de la fuente o la calibración no permiten una medición confiable, el sistema debe rechazar el procesamiento métrico y permitir continuar únicamente en modo no métrico o manual.

## 6. Arquitectura propuesta

### 6.1 Principio

Para el MVP se utilizará un backend modular y un worker de procesamiento. No se introducirá una arquitectura de microservicios completa hasta que existan necesidades de escala, equipos separados o despliegues independientes.

```text
Frontend web responsive
        |
        | HTTPS / API
        v
Backend modular
  - expedientes y permisos
  - versiones y auditoría
  - calibración y modelo canónico
  - exportaciones
        |
        +--> PostgreSQL
        +--> almacenamiento de objetos S3-compatible
        +--> worker Python para imagen, OCR y geometría
        +--> cola de tareas cuando el volumen lo justifique
```

### 6.2 Componentes

- **Frontend:** React con editor 2D basado preferentemente en SVG. Canvas puede incorporarse si las pruebas de rendimiento lo justifican. Three.js no se utilizará mientras el alcance sea exclusivamente 2D.
- **Backend:** Java con Spring Boot si la organización exige ese ecosistema; de lo contrario, un backend Python modular reduce duplicación con el worker. La decisión debe tomarse antes de comenzar la implementación.
- **Worker:** Python para procesamiento de imagen, OCR, geometría y generación DXF.
- **Base de datos:** PostgreSQL. PostGIS es opcional y solo se habilita si se requiere CRS, coordenadas geográficas o consultas espaciales institucionales.
- **Almacenamiento:** una única implementación S3-compatible, MinIO autoalojado o AWS S3. Se requiere versionado y retención; no se deben configurar ambos como si fueran un mismo componente.
- **Cola:** procesamiento en segundo plano. Para el MVP puede comenzar como una cola interna o un worker único; se incorpora un broker cuando la carga real lo requiera.

### 6.3 Aplicación de campo

La interfaz debe ser usable en tablet y PC. El modo offline completo no forma parte del MVP. Si se incorpora posteriormente, deberá definir cifrado local, expiración, sincronización, resolución de conflictos y eliminación segura.

## 7. Modelo geométrico canónico

DXF y PDF no son el modelo de negocio. Ambos se generan desde una estructura intermedia versionada.

Cada entidad debe contener como mínimo:

```json
{
  "id": "entity-id",
  "type": "line | polyline | point | symbol | text | dimension",
  "geometry": "coordinates in local metric system",
  "layer": "L_STRUCTURE",
  "unit": "m",
  "source": {
    "artifact_id": "original-image-id",
    "region": "image coordinates",
    "method": "manual | cv | ocr | model"
  },
  "confidence": 0.0,
  "model_version": "optional",
  "review_status": "suggested | accepted | corrected | rejected",
  "created_by": "user-or-system",
  "revision": 1
}
```

El modelo debe conservar la geometría en unidades reales y, cuando corresponda, la relación con la imagen fuente. Nunca se deben guardar únicamente píxeles como representación final.

## 8. Calibración y metrología

### 8.1 Reglas

- Una relación global `px/m` solo es válida para una imagen sin perspectiva apreciable y una superficie plana.
- Una fotografía oblicua debe corregirse antes de medir mediante puntos de control y una transformación planar.
- La rectificación no corrige objetos fuera del plano del croquis ni errores del levantamiento original.
- El sistema debe diferenciar `métrico`, `aproximado` y `no_métrico`.
- Toda medida debe incluir unidad, fuente, método y error estimado.

### 8.2 Métodos admitidos

1. **Escaneo frontal:** una distancia conocida y validación de escala uniforme.
2. **Fotografía de plano:** cuatro puntos del contorno o referencias equivalentes para homografía; registrar los puntos y la matriz de transformación.
3. **Control múltiple:** dos o más distancias o puntos conocidos para detectar inconsistencias.
4. **Coordenadas locales:** origen, eje X, eje Y, Norte y unidad definidos por el usuario.

El usuario debe poder visualizar la referencia, la escala, los ejes y las medidas resultantes antes de aceptar la calibración.

### 8.3 Incertidumbre

La especificación no fija todavía una tolerancia universal. La institución usuaria debe definirla por tipo de levantamiento. El sistema debe reportar como mínimo:

- error de reproyección de los puntos de control;
- diferencia entre distancias conocidas y calculadas;
- error máximo y RMS cuando existan varias referencias;
- advertencia si las referencias son incompatibles.

## 9. Procesamiento asistido por IA

### 9.1 Capacidades iniciales

- limpieza y mejora de la imagen;
- detección sugerida de líneas y polilíneas;
- OCR de textos y cotas;
- clasificación de un catálogo reducido de símbolos institucionales;
- sugerencia de capas y etiquetas.

### 9.2 Límites

La salida de IA es una propuesta editable. Cada resultado debe incluir confianza, versión del modelo, fecha, parámetros relevantes y región de origen. Las entidades con baja confianza deben destacarse y no deben bloquear la revisión.

El sistema no debe modificar silenciosamente las decisiones del usuario ni recalcular una geometría validada sin crear una nueva revisión.

### 9.3 Dataset y validación

Los 50-100 croquis iniciales pueden servir para descubrir variabilidad y diseñar anotaciones, pero no bastan para afirmar rendimiento general. Antes de entrenar o aceptar un modelo se requiere:

- autorización de uso y anonimización;
- guía de anotación;
- separación por expediente entre entrenamiento, validación y prueba;
- clases y casos difíciles definidos;
- evaluación por tipo de croquis y calidad de imagen;
- registro de versión de datos, modelo y código;
- prueba de regresión con un conjunto congelado.

Las métricas mínimas serán precisión, recall y F1 por clase; error geométrico en unidades reales; exactitud de OCR numérico; porcentaje de casos derivados a revisión manual; y tiempo de procesamiento p95.

No se fija un umbral de “80%” sin definir clase, conjunto de prueba, tolerancia geométrica y consecuencia operativa.

## 10. Catálogo de capas y símbolos

La nomenclatura será única para todos los tipos de expediente:

| Capa | Uso |
|---|---|
| `L_STRUCTURE` | Muros, bordes, límites y geometría principal |
| `L_ACCESS` | Puertas, ventanas, accesos y circulación |
| `L_EVIDENCE` | Indicadores de evidencias y objetos documentados |
| `L_DIMENSIONS` | Cotas y referencias métricas |
| `L_TEXT` | Etiquetas y notas |
| `L_ORIENTATION` | Norte, ejes y referencias de orientación |
| `L_ANNOTATIONS` | Observaciones del técnico o revisor |
| `L_CONTROL` | Puntos de control y elementos de calibración |

Los nombres, colores, tipos de línea, unidades y bloques deben estar versionados en un catálogo institucional. Las capas específicas de un caso no se crean ad hoc durante la exportación.

## 11. Auditoría, seguridad y cadena de custodia

### 11.1 Preservación

- El original se almacena una sola vez como objeto inmutable y versionado.
- Se calcula SHA-256 del original al ingresar.
- Cada versión del modelo, DXF, PDF, manifiesto y ZIP recibe su propio hash.
- El sistema conserva metadatos de creación, transferencia, acceso, exportación y cierre.
- La cadena de custodia se representa como eventos: quién, qué, cuándo, desde dónde, con qué propósito y qué hash resultó.

### 11.2 Firma y manifiesto

El hash prueba integridad de un archivo; no identifica por sí solo al firmante ni demuestra toda la cadena de custodia. El paquete final debe incluir un manifiesto canónico con:

- identificador del expediente;
- lista de artefactos y hashes;
- versiones de software y modelos;
- calibración y sistema de coordenadas;
- historial de validación;
- identidad y rol del aprobador;
- fecha y hora con zona horaria;
- firma digital o mecanismo institucional equivalente.

El PDF de auditoría es una representación legible del manifiesto, no el sustituto de los registros originales.

### 11.3 Seguridad

- TLS en tránsito y cifrado en reposo.
- RBAC por expediente y organización.
- MFA para roles de aprobación y administración.
- Principio de mínimo privilegio.
- Registro de accesos y descargas.
- Separación entre original, borrador y versión final.
- Retención, legal hold y eliminación según la normativa institucional.
- Copias de respaldo con prueba periódica de restauración.
- Prohibición de enviar imágenes sensibles a servicios externos de IA sin autorización explícita.

## 12. Edición y estados del expediente

Estados mínimos:

```text
CREATED
SOURCE_INGESTED
CALIBRATION_PENDING
PROCESSING
REVIEW_PENDING
IN_REVIEW
VALIDATED
EXPORTED
SEALED
REOPENED
REJECTED
```

Solo un usuario autorizado puede pasar de `IN_REVIEW` a `VALIDATED`. Una modificación posterior debe crear una nueva revisión y devolver el expediente a revisión; nunca debe sobrescribir la versión sellada.

## 13. Requisitos funcionales

### RF-01. Expedientes

El sistema debe crear expedientes con identificador único, tipo de hecho, fecha, organización, ubicación declarada, usuarios autorizados y estado.

### RF-02. Ingesta

Debe aceptar JPEG, PNG, TIFF y PDF de una sola página, registrar metadatos disponibles, calcular hash y conservar el original sin modificación.

### RF-03. Control de calidad

Debe informar problemas de resolución, desenfoque, perspectiva, recorte, contraste o legibilidad antes de procesar.

### RF-04. Calibración

Debe permitir los métodos definidos en la sección 8, registrar unidad, puntos, referencias, transformación, error y aprobación del usuario.

### RF-05. Procesamiento

Debe ejecutar tareas asíncronas, mostrar estado, conservar errores y permitir reintento sin duplicar el original.

### RF-06. Revisión

Debe mostrar la imagen original y el modelo vectorial superpuesto, permitir agregar, editar, eliminar y reclasificar entidades, y mostrar confianza y procedencia.

### RF-07. Auditoría

Cada cambio debe registrar usuario, rol, fecha, entidad, valor anterior, valor nuevo, motivo opcional y revisión resultante.

### RF-08. Validación

Debe exigir revisión humana y permitir bloquear una revisión validada.

### RF-09. Exportación

Debe generar DXF, PDF, manifiesto y reporte legible desde la misma revisión validada.

### RF-10. Entrega

La descarga del paquete debe requerir autorización, quedar auditada y mostrar el contenido antes de confirmar.

## 14. Exportación CAD y PDF

### 14.1 DXF

- Unidades métricas explícitas.
- Modelo 1:1 en espacio de modelo.
- Capas del catálogo institucional.
- Polilíneas para geometrías lineales.
- Bloques atributados para símbolos repetibles.
- Textos y cotas con estilos configurados.
- Versión inicial objetivo: DXF R2013, salvo que la institución defina otra.

`ezdxf` se utilizará para crear el DXF. La validación debe incluir lectura posterior, auditoría estructural y apertura comprobada en las aplicaciones CAD que la institución realmente utilice.

### 14.2 PDF técnico

El PDF se generará desde el modelo canónico con un renderer independiente de DXF. Debe incluir:

- plano a escala;
- escala numérica y gráfica;
- Norte y sistema de coordenadas;
- membrete institucional;
- leyenda de símbolos;
- identificador de expediente;
- versión de la revisión;
- advertencia sobre elementos sugeridos o no validados, cuando corresponda.

Debe comprobarse que la escala impresa sea correcta en los formatos de salida definidos, como A4 y A3.

## 15. Criterios de aceptación del MVP

### CA-01. Original

Al cargar un archivo, el sistema conserva el contenido original, calcula su SHA-256 y permite verificarlo posteriormente.

### CA-02. Calibración

Una prueba con geometría conocida reproduce las distancias dentro de la tolerancia definida por la institución y muestra el error de calibración. Una fotografía oblicua no puede aprobarse como métrica sin rectificación.

### CA-03. Revisión humana

Una sugerencia de IA puede aceptarse, corregirse, rechazarse o reemplazarse manualmente. La versión final conserva la procedencia y el historial de la decisión.

### CA-04. Exportación

El DXF se puede leer con el parser utilizado por el sistema y abrir en los visores CAD objetivo sin errores. Las capas, unidades y entidades coinciden con el modelo canónico.

### CA-05. Escala

Las distancias verificadas en el modelo, DXF y PDF coinciden dentro de la tolerancia definida. La prueba debe realizarse con al menos una geometría horizontal, una vertical y una diagonal.

### CA-06. Auditoría

La modificación de una entidad genera un nuevo evento, nueva revisión y nuevo hash de los artefactos derivados. La versión sellada permanece recuperable.

### CA-07. Fallos

Una imagen ilegible, una calibración inconsistente o una tarea fallida producen un estado explícito y accionable. El sistema no genera un resultado aparentemente confiable por defecto.

### CA-08. Seguridad

Un usuario sin autorización no puede consultar, modificar, exportar ni descargar un expediente ajeno. Cada intento queda registrado.

## 16. Requisitos no funcionales

- Todas las fechas se almacenan en UTC y se muestran en la zona horaria institucional.
- La interfaz y los informes deben soportar español, unidades métricas y coma decimal cuando corresponda.
- El sistema debe funcionar en tablets y navegadores soportados por la organización.
- El procesamiento debe ser asíncrono y mostrar progreso o estado, nunca una espera indefinida.
- Los artefactos deben poder reproducirse indicando versión de código, configuración y modelo.
- Deben existir pruebas de restauración de respaldo y de recuperación ante fallo de worker.
- Debe existir una política documentada de retención y borrado.

## 17. Plan de implementación

### Fase 0: Descubrimiento

- Entrevistas con planimetristas, revisores y custodios.
- Definición de tolerancias y formatos institucionales.
- Catálogo de capas y símbolos.
- Dataset autorizado y guía de anotación.
- Decisión final sobre Java o Python como backend.

### Fase 1: Vertical slice manual

- Expediente, ingesta, hash, calibración manual, editor 2D y exportación DXF/PDF.
- Auditoría de cambios y paquete de entrega.
- Sin IA automática, salvo controles básicos de imagen.

### Fase 2: Asistencia de IA

- Detección de líneas.
- OCR de textos y cotas.
- Catálogo reducido de símbolos.
- Métricas y revisión con conjunto congelado.

### Fase 3: Operación institucional

- Integración de identidad y almacenamiento institucional.
- Retención, legal hold, firmas y respaldos.
- Pruebas de interoperabilidad con las aplicaciones CAD objetivo.
- Evaluación de modo offline y capacidades avanzadas solo si existe evidencia de necesidad.

## 18. Decisiones que debe tomar la institución

Antes de comenzar el desarrollo deben quedar documentados:

1. Jurisdicción y requisitos legales aplicables.
2. Tolerancias métricas por tipo de levantamiento.
3. Formatos CAD y versiones realmente utilizadas.
4. Catálogo oficial de símbolos y capas.
5. Roles autorizados para revisar, aprobar, sellar y entregar.
6. Plazos de retención y procedimiento de legal hold.
7. Política sobre procesamiento externo de imágenes.
8. Formato de firma digital y autoridad de tiempo, si corresponde.

## 19. Referencias técnicas

- OpenCV, homografía y corrección de perspectiva: <https://docs.opencv.org/4.x/d9/dab/tutorial_homography.html>
- ezdxf, versiones DXF, unidades y límites del paquete: <https://ezdxf.readthedocs.io/en/stable/introduction.html>
- NIST, preservación de evidencia digital: <https://nvlpubs.nist.gov/nistpubs/ir/2022/NIST.IR.8387.pdf>
- NIST, definición de cadena de custodia: <https://csrc.nist.gov/glossary/term/chain_of_CUSTODY>
- Ultralytics, documentación y licenciamiento: <https://www.ultralytics.com/license>
- AWS S3 Object Lock: <https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html>
- PostGIS, geometría local y sistemas de referencia: <https://postgis.net/documentation/faq/geometry-or-geography/>

## 20. Nota de validez

Sketch2CAD es una herramienta de documentación y asistencia técnica. La validez probatoria, la admisibilidad del resultado, el régimen de firma y la cadena de custodia deben ser revisados y aprobados por la autoridad jurídica e institucional competente antes de utilizar el sistema en producción.
