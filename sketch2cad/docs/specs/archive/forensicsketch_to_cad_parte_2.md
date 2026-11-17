## 3. Casos de Uso de Aceptación y Criterios de Validación

Esta batería de casos de uso servirá como marco de verificación para evaluar si la aplicación cumple con los objetivos funcionales y operativos al finalizar el desarrollo.

---

### Caso de Uso 01: Reconstrucción Plani-CAD de Siniestro Vial en Cruce Urbano (Intersección)

* **Descripción:** El técnico carga un croquis de campo tomado en una intersección vial donde ocurrió una colisión entre dos vehículos. La app debe vectorizar la geometría de la vía, ubicar la simbología de las evidencias (vehículos, huellas de frenado, semáforo, restos) e interpretar las cotas de distancia.
* **Actor Principal:** Técnico / Perito en Accidentología y Planimetría Vial.
* **Precondiciones:**
  * Usuario autenticado con rol Perito.
  * Expediente activo en el sistema.
  * Imagen del croquis con trazo visible, indicador del Norte y al menos una medida de cota principal (ej. ancho de calzada = 7.50 m).

#### Flujo Principal:
1. El técnico selecciona el expediente e ingresa al módulo **Nuevo Plano**.
2. Sube la fotografía/escaneo del croquis del siniestro vial.
3. El sistema solicita la **Calibración Métrica**: el técnico marca los dos extremos del ancho de la calle en la imagen e ingresa el valor real `7.50 m`.
4. El microservicio de IA (Python) procesa la imagen:
   * **OpenCV/Vectorización:** Detecta las líneas continuas de las aceras, esquinas de la intersección y bordes de la calzada.
   * **YOLO:** Identifica los símbolos de Vehículo A, Vehículo B, Inicio/Fin de Huella de Frenado y Posición del Semáforo.
   * **OCR/IDP:** Extrae los valores numéricos manuscritos asociados a los vectores de distancia.
5. El sistema presenta la superposición gráfica (vector sobre mapa de bits).
6. El backend en Java genera el borrador estructurado en capas (`L_CALZADA`, `L_VEHICULOS`, `L_EVIDENCIAS`, `L_COTAS`).
7. El técnico valida los elementos, confirma la orientación del Norte y presiona **Exportar CAD**.
8. El sistema genera el archivo `.dxf` final e imprime el sello de hash de cadena de custodia digital.

* **Criterios de Aceptación:**
  * El archivo `.dxf` abre correctamente en AutoCAD / LibreCAD con la escala 1:1 respetando los 7.50 m calibrados.
  * Las entidades están separadas en capas (*layers*) independientes.
  * Las huellas de frenado se exportan como políneas y los vehículos como bloques CAD.

---

### Caso de Uso 02: Documentación y Reconstrucción de Escena del Crimen en Interiores (Homicidio)

* **Descripción:** Levantamiento planimétrico de un recinto cerrado (habitación) que contiene una víctima, elementos balísticos (casquillos, impactos en pared) y manchas de sangre.
* **Actor Principal:** Técnico en Criminalística / Perito Planimetrista.
* **Precondiciones:**
  * Croquis con vista cenital de la habitación, indicando puertas, ventanas, ubicación del cuerpo y cotas trianguladas desde dos esquinas fijas hacia cada evidencia.

#### Flujo Principal:
1. El usuario sube el boceto a mano alzada del recinto interior.
2. Define la cota de calibración de referencia (ej. longitud de la pared norte = 4.20 m).
3. El motor de IA ejecuta el reconocimiento especializado:
   * Identifica el perímetro ortogonal de las paredes y aberturas (ventanas/puertas con sus arcos de apertura).
   * Reconoce la simbología forense específica: contorno de víctima, casquillos (.9mm / .45), manchas de proyección de sangre e impactos de bala.
   * Lee la tabla o leyenda de referencia manuscrita en el margen del papel.
4. El editor interactivo despliega las evidencias vectorizadas con etiquetas editables.
5. El perito verifica la posición de las evidencias balísticas respecto a las paredes.
6. Se aprueba la reconstrucción y el sistema genera el paquete pericial: archivo `.dxf` + reporte en `.pdf` con tabla de evidencias georreferenciadas.

* **Criterios de Aceptación:**
  * Detección precisa de aberturas de puertas y ventanas dentro de la envolvente del muro.
  * Separación correcta en capas (`L_MUROS`, `L_VICTIMA`, `L_BALISTICA`, `L_MANCHAS`).
  * Inserción de bloques atributados con la numeración de cada indicio/evidencia.

---

### Caso de Uso 03: Edición y Corrección Manual mediante Interfaz de Validación (*Human-in-the-Loop*)

* **Descripción:** Debido a la mala calidad del trazo o desgaste del papel en campo, la IA interpreta erróneamente un símbolo o ignora una cota manuscrita. El perito utiliza la interfaz web para corregir, ajustar o añadir elementos antes de la exportación final.
* **Actor Principal:** Perito Supervisor / Técnico Planimetrista.
* **Precondiciones:**
  * El croquis ha sido procesado por el motor de IA y se encuentra en estado **Pendiente de Validación**.

#### Flujo Principal:
1. El técnico abre la pantalla de validación dual (vista izquierda: croquis original en alta resolución; vista derecha: canvas vectorial dinámico).
2. El sistema resalta en color naranja los elementos con bajo nivel de certeza algorítmica (*confidence score < 70%*).
3. El técnico realiza las siguientes correcciones:
   * **Re-etiquetado:** Selecciona una entidad erróneamente identificada como "Mueble" y la reasigna a "Vehículo Estacionado".
   * **Ajuste de Vértice:** Arrastra un punto de la calzada para corregir un ángulo que no era perfectamente perpendicular en el dibujo original.
   * **Edición de Cota:** Hace doble clic en un texto OCR mal leído ("3.50" en lugar de "3.80") e introduce el valor numérico correcto.
4. Presiona **Guardar y Recalcular Geometría**.
5. El backend Java registra en el *log* de auditoría la modificación realizada (usuario, fecha, entidad modificada, valor anterior y valor nuevo).
6. Se autoriza la generación del archivo `.dxf` consolidado.

* **Criterios de Aceptación:**
  * Las modificaciones hechas por el usuario prevalecen al 100% sobre el resultado del modelo de IA.
  * Toda edición queda registrada inmutablemente en la base de datos PostgreSQL para garantizar la transparencia pericial en el juicio.
  * La interfaz responde fluidamente al arrastrar y soltar nodos vectoriales.

---

### Caso de Uso 04: Exportación Multiformato y Firma de Cadena de Custodia Digital

* **Descripción:** Una vez validado el plano, el sistema emite el archivo CAD final (`.dxf`), un plano técnico vectorial listo para impresión (`.pdf`) y emite un certificado digital con el código HASH de la información para validez judicial.
* **Actor Principal:** Perito Judicial / Encargado de Custodia de Pruebas.
* **Precondiciones:**
  * El plano ha superado el proceso de validación del Caso de Uso 03.

#### Flujo Principal:
1. El perito selecciona el botón **Finalizar y Cerrar Expediente Plani-CAD**.
2. El backend Java solicita al microservicio Python la compilación final del mapa de vectores mediante la librería `ezdxf`.
3. El sistema genera de forma simultánea:
   * **Archivo DXF:** Compatible con versiones AutoCAD 2013 en adelante, estructurado en capas y bloques estandarizados.
   * **Archivo PDF:** Plano normalizado a escala (A3/A4) con membrete institucional, marco, escala gráfica, rosa de los vientos (Norte) y cuadro de convenciones/leyendas.
4. El servicio Java calcula la firma de control (`SHA-256`) sobre la combinación de la imagen de entrada original y el archivo `.dxf` generado.
5. Se almacena el paquete final en el almacenamiento de objetos (MinIO/S3) y se actualiza el registro en PostgreSQL.
6. El usuario descarga un archivo comprimido `.zip` con el expediente completo listo para remitir a la fiscalía.

* **Criterios de Aceptación:**
  * El archivo `.dxf` exportado abre sin errores de sintaxis en múltiples visores CAD (AutoCAD, QCAD, LibreCAD).
  * El PDF generado mantiene el rigor formal (cuadro de datos del caso, firma digital, escala, simbología).
  * El Hash SHA-256 garantiza que el archivo CAD no ha sido alterado desde su emisión.

---

## 4. Próximos Pasos Recomendados para la Fase de Inicio

1. **Recolección de Dataset Inicial:** Reunir entre 50 y 100 croquis escaneados reales (anonimizados) de siniestros viales y escenas del crimen para entrenar el modelo YOLO de detección de símbolos y ajustar el OCR.
2. **Definición del Estándar de Capas CAD:** Formalizar la convención de nombres de capas (`LAYERS`) y colores estándar que exigirá la policía / fiscalía local.
3. **Prototipado de la Interfaz (UX/UI):** Diseñar la pantalla de validación en React con el visor canvas de doble panel para probar la usabilidad con peritos reales.