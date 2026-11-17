# Especificación de Proyecto: ForensicSketch-to-CAD

**Sistema de Reconstrucción Plani-CAD Forense asistido por Inteligencia Artificial**

---

## 1. Visión General del Proyecto

### 1.1 Contexto y Antecedentes
En la práctica forense y policial actual, la documentación inicial del lugar de los hechos (siniestros viales, escenas del crimen, inspecciones oculares) la realizan técnicos planimetristas mediante notas de campo, bocetos a mano alzada, croquis y esquemas manuales. La conversión posterior de estos elementos a planos CAD estandarizados suele ser un proceso manual propenso a demoras, errores de transcripción o inconsistencias de escala, lo cual impacta negativamente en los tiempos de la investigación penal.

Estudios recientes en el área (como la incorporación del Procesamiento Inteligente de Documentos - IDP y el análisis de visión computacional en planimetría forense) han demostrado que la automatización del flujo de trabajo no solo reduce significativamente el margen de error humano, sino que también acelera los tiempos de respuesta pericial y refuerza la integridad de la cadena de custodia digital.

### 1.2 Objetivo
Desarrollar una aplicación de software modular e inteligente que permita a los técnicos en planimetría legal transformar de forma rápida y asistida sus croquis, bocetos a mano alzada y notas de campo en planos digitales estandarizados en formato CAD (`.dxf`), garantizando la precisión métrica, la supervisión humana (*Human-in-the-Loop*) y el cumplimiento de los estándares técnico-legales.

---

## 2. Requerimientos Funcionales y Arquitectura de Software

### 2.1 Arquitectura General del Sistema
El sistema adopta una arquitectura de microservicios desacoplados:
- **Backend Principal (Java):** Encargado de la lógica de negocio, autenticación, control de expedientes, auditoría, orquestación de tareas y gestión de almacenamiento.
- **Motor de Inteligencia Artificial (Python):** Dedicado exclusivamente al procesamiento de imágenes, visión computacional (detección de simbología y vectorización de trazos), OCR/IDP y generación técnica de datos geométricos.

```
       +-----------------------------------------------+
       |             Frontend (React / PWA)             |
       +-----------------------+-----------------------+
                               | (HTTP / REST)
                               v
       +-----------------------------------------------+
       |            Backend Core (Java)                |
       |  (Spring Boot / Quarkus + PostGIS + MinIO)     |
       +-----------------------+-----------------------+
                               | (HTTP / Broker API)
                               v
       +-----------------------------------------------+
       |           Servicio de IA (Python)             |
       |  (FastAPI + OpenCV + YOLO + ezdxf + IDP)      |
       +-----------------------------------------------+
```

### 2.2 Stack Tecnológico Seleccionado

| Capa | Tecnología Seleccionada | Justificación / Rol |
| :--- | :--- | :--- |
| **Frontend** | React / Next.js (PWA) | Interfaz web interactiva optimizada para tablets y PCs. Permite visualización de canvas en capa e interacción táctil en campo o laboratorio. |
| **Visualización Canvas** | Canvas API / Three.js | Renderización dinámica del croquis original superpuesto con los vectores e iconos detectados. |
| **Backend Core** | Java (Spring Boot) | Orquestador principal, gestión de usuarios/roles (RBAC), control estricto de auditoría legal y API REST segura. |
| **Servicio IA / Visión**| Python (FastAPI) | Microservicio especializado en modelos de visión computacional, OCR y procesamiento de imágenes. |
| **Procesamiento de Imagen**| OpenCV + Shapely | Corrección de perspectiva, binarización de trazos manuscritos y cálculos de geometría plana. |
| **Detección de Símbolos**| YOLO (v8/v11) | Reconocimiento de simbología forense (balística, cuerpos, manchas de sangre, orientación, mobiliario). |
| **OCR / IDP** | PaddleOCR / Tesseract | Lectura de acotaciones numéricas, leyendas y notas escritas a mano en el croquis. |
| **Generación CAD** | `ezdxf` (Python) | Construcción nativa de entidades geométricas en capas estandarizadas para exportación en `.dxf`. |
| **Base de Datos** | PostgreSQL + PostGIS | Almacenamiento relacional de expedientes, registros de cambios y metadatos con soporte de geometría espacial. |
| **Almacenamiento** | MinIO / AWS S3 | Almacenamiento seguro de imágenes escaneadas originales y archivos CAD generados. |

---

## 3. Flujo de Trabajo Técnico (Workflow)

1. **Ingesta e Identificación del Caso:** El técnico crea/selecciona un expediente en la plataforma y sube la imagen (fotografía o escaneo) del croquis a mano alzada.
2. **Calibración Metric/Escala:** El usuario define una línea de referencia en la imagen e ingresa su longitud real (ej. $5.00\text{ m}$).
3. **Procesamiento Asíncrono de IA:**
   - Binarización y limpieza del trazo a mano alzada.
   - Vectorización de paredes, límites y geometrías principales.
   - Detección de símbolos forenses normalizados.
   - Extracción de texto y cotas mediante OCR.
4. **Validación Human-in-the-Loop:** El perito revisa el lienzo interactivo, ajusta la posición de puntos o corrige valores interpretados erróneamente.
5. **Compilación y Exportación CAD:** El sistema genera las entidades CAD estructuradas en capas (*Layers*) y exporta el archivo en `.dxf`, `.pdf` vectorial y reporte con metadatos de auditoría.

---

## 4. Casos de Uso Básicos (Criterios de Aceptación)

Para determinar la finalización exitosa del desarrollo, el sistema deberá cumplir satisfactoriamente con la siguiente batería de casos de uso:

### CU-01: Ingesta y Calibración Metrológica
- **Actor:** Técnico en Planimetría Forense.
- **Precondición:** El técnico ha iniciado sesión y tiene un expediente abierto.
- **Flujo Principal:**
  1. El usuario selecciona "Cargar Nuevo Croquis" y sube una fotografía/escaneo de un plano dibujado a mano.
  2. El sistema muestra la imagen cargada en un canvas interactivo.
  3. El usuario marca dos puntos sobre la imagen indicando una distancia de referencia y digita el valor en metros (ej. "3.50 m").
  4. El sistema calcula la relación de pixel por metro ($\text{px}/\text{m}$) y confirma la calibración de la escala.
- **Criterio de Aceptación:** El valor de escala se guarda y todas las distancias medidas digitalmente sobre el canvas corresponden con precisión a la proporción definida.

### CU-02: Extracción Automatizada e Interpretación con IA
- **Actor:** Sistema (Backend Java + Servicio IA Python).
- **Precondición:** Se ha cargado y calibrado la imagen del croquis.
- **Flujo Principal:**
  1. El backend envía la orden de procesamiento al microservicio de IA en Python.
  2. El modelo de visión computacional detecta los contornos de la infraestructura (muros, accesos) y vectoriza las líneas.
  3. El modelo de detección de objetos identifica la simbología (ej. casquillos balísticos, vehículo, orientación Norte).
  4. El motor OCR extrae los textos de cotas (ej. "2.10m") y etiquetas ("Evidencia #1").
  5. El sistema devuelve al frontend las entidades vectoriales categorizadas en capas preliminares.
- **Criterio de Aceptación:** El sistema identifica correctamente al menos el 80% de las líneas estructurales y los símbolos estandarizados presentes en el boceto manual sin deformaciones métricas.

### CU-03: Edición y Validación Humana (*Human-in-the-Loop*)
- **Actor:** Técnico en Planimetría Forense.
- **Precondición:** La IA ha completado el procesamiento y muestra las entidades superpuestas al boceto original.
- **Flujo Principal:**
  1. El técnico visualiza la propuesta vectorial sobre el croquis escaneado.
  2. El técnico arrastra un vértice para corregir un muro mal interpretado.
  3. El técnico modifica un texto de cota mal leído por el OCR.
  4. El técnico confirma la validación del plano.
- **Criterio de Aceptación:** Cualquier cambio realizado por el técnico modifica la geometría interna del modelo y queda registrado en el historial de modificaciones (log de auditoría).

### CU-04: Exportación de Archivo CAD Estructurado (.DXF)
- **Actor:** Técnico en Planimetría Forense.
- **Precondición:** El plano ha sido validado por el técnico.
- **Flujo Principal:**
  1. El técnico hace clic en "Exportar a CAD".
  2. El microservicio en Python procesa la estructura de datos mediante `ezdxf`.
  3. Se genera un archivo `.dxf` donde las entidades están separadas en capas estándar (`ESTRUCTURA`, `EVIDENCIAS`, `COTAS`, `NOTAS_TEXTO`).
  4. El archivo se almacena en el repositorio seguro y se descarga en el equipo del usuario.
- **Criterio de Aceptación:** El archivo `.dxf` generado puede abrirse sin errores de sintaxis en programas CAD estándar (AutoCAD, LibreCAD, QCAD), manteniendo la escala métrica real y la separación de capas correcta.

### CU-05: Auditoría y Garantía de Cadena de Custodia Digital
- **Actor:** Perito Inspector / Sistema.
- **Precondición:** Se ha exportado el plano final del siniestro o escena del crimen.
- **Flujo Principal:**
  1. El backend en Java genera un hash criptográfico (SHA-256) asociado al plano CAD resultante, a la imagen original cargada y a las credenciales del técnico firmante.
  2. Se expide un reporte de auditoría en PDF con los metadatos del caso, sello de tiempo, histórico de ediciones y firmas.
- **Criterio de Aceptación:** El PDF de auditoría registra de manera inmutable el origen de la imagen y los cambios aplicados en el software, asegurando la validez legal y procesal de la evidencia gráfica.

---

## 5. Próximos Pasos Recomendados

1. **Recolección de Dataset Forense:** Recopilar y etiquetar una muestra inicial de croquis a mano alzada reales o simulados para entrenamiento del modelo de detección de símbolos (YOLO).
2. **Prototipado de Interfaz de Calibración:** Implementar el primer prototipo del canvas en React para probar la precisión de la calibración métrica lineal.
3. **Definición del Esquema de Capas CAD:** Normalizar la nomenclatura de *Layers* CAD de acuerdo con las normativas periciales de la institución destinataria.