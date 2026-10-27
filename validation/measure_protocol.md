# Protocolo de medición de tiempos (G-1 y G-3)

Tareas 001-116 (G-1) y 001-118 (G-3), REQ-20. El resultado es una **línea base medida**, no una prueba de aprobado o suspenso (PRD DEC-4: no hay umbral en v1). La medición es interna: la hace el dueño del proyecto sobre sus propios croquis de `validation/sketches/`.

## Qué se mide

- **G-1**: minutos para pasar un croquis a un DXF exportado con Sketch2CAD y con FreeCAD, mismo croquis, misma persona.
- **G-3**: minutos con Sketch2CAD con sugerencias y sin sugerencias, y porcentaje de polilíneas sugeridas aceptadas sin cambios.

## Cómo se cuenta el tiempo

- **Empieza** al abrir el croquis: en Sketch2CAD, al pulsar «Subir» con el archivo del croquis ya elegido; en FreeCAD, al abrir la imagen del croquis (o el documento nuevo con ella como referencia).
- **Termina** cuando el DXF está exportado y guardado en disco: en Sketch2CAD, al terminar la descarga del borrador o del paquete final; en FreeCAD, al cerrar el diálogo de exportación a DXF.
- Reloj: cronómetro (el del móvil sirve) iniciado y parado por la misma persona que convierte. Se anota en **minutos con un decimal** (12 min 40 s = 12,7).
- Cuenta todo lo que hay entre medio: calibrar, dibujar, fijar cotas, revisar, corregir errores y esperar al servidor.

## Qué cuenta como «terminado»

El DXF exportado contiene todas las cotas declaradas del croquis (tabla del README) con su valor. Se comprueba después con la revisión de G-2 (`dimension_check.csv`, misma regla de coincidencia). Si falta una cota o tiene otro valor, la conversión no queda invalidada, pero se anota en `notes` (`cota d3 ausente`), y G-2 lo cuenta como no coincidente.

## Condiciones de cada método

- **Sketch2CAD (G-1)**: configuración por defecto del despliegue local (`docker compose`), es decir, `ai_assist.enabled = true` (valor por defecto de `AI_ASSIST_ENABLED`) y los parámetros de `suggest_lines`, QC y OCR sin sobreescrituras. Se puede usar todo lo que ofrece la aplicación (sugerencias, OCR, herramientas métricas).
- **FreeCAD (G-1)**: FreeCAD 1.1 con un documento nuevo vacío y el banco de trabajo Draft o Sketcher, a elección de la persona pero siempre el mismo. Se puede insertar la foto del croquis como imagen de referencia y escalarla, y usar una plantilla con las capas de Sketch2CAD preparada **antes** de empezar (preparar la plantilla no cuenta). No se puede partir de un plano anterior del mismo croquis.
- **G-3, «con sugerencias»**: es la misma conversión de Sketch2CAD de G-1 (no se repite). **«Sin sugerencias»**: otra conversión del mismo croquis con la asistencia desactivada (`PUT /api/v1/config/params/ai_assist.enabled` con `{"value": false, "scope": "global"}` como administrador, o `AI_ASSIST_ENABLED=false` y reiniciar); al terminar se vuelve a activar. El porcentaje de aceptación se obtiene del plano de la conversión «con sugerencias» con `query_suggestion_acceptance.py`, antes de reabrirlo.

## Orden y contrapeso del aprendizaje

- Croquis impares: primero FreeCAD; croquis pares: primero Sketch2CAD (`first_method` en `timings.csv`).
- No convertir el mismo croquis con los dos métodos el mismo día si se puede evitar; si no, dejar al menos 2 horas entre ambas. Descanso de al menos 10 minutos entre dos conversiones seguidas.
- La conversión «sin sugerencias» de G-3 va siempre después de las dos de G-1, en otro día. Es la tercera vez que la persona pasa ese croquis, así que el aprendizaje la favorece: la comparación con / sin sugerencias está sesgada en contra de las sugerencias y se declara así en el informe.
- No mirar el resultado de la conversión anterior del mismo croquis durante la siguiente.

## Interrupciones, fallos y abandono

- Una interrupción ajena (llamada, timbre) para el cronómetro y se reanuda al volver; si dura más de 15 minutos, la conversión se repite otro día desde cero y se anota `repetida` en `notes`.
- Un fallo de la aplicación (error, job fallido) **no** para el cronómetro: es parte del tiempo. Se anota en `notes` con la causa mostrada (`job suggest_lines fallido`).
- Si a los 120 minutos no hay DXF, o la persona abandona, se escribe `abandoned` en la columna de tiempo de ese método y el motivo en `notes`. `compute_g1.py` cuenta esas filas pero las deja fuera del cociente.

## Quién mide y qué se anota

- Mide y convierte la misma persona (el dueño), con iniciales en `person`. La revisión de cotas de G-2 la hace otra persona.
- `timings.csv` está en formato ancho: **una fila por croquis con los dos tiempos y sus fechas** (`sketch,practice,first_method,minutes_sketch2cad,minutes_freecad,date_sketch2cad,date_freecad,person,notes`; fechas `AAAA-MM-DD`). Se usan dos columnas de fecha porque cada método se mide en un día distinto.
- `suggestions.csv`: una fila por croquis (`sketch,polylines_total,accepted_unchanged,accepted_unchanged_pct,minutes_with_suggestions,minutes_without_suggestions,notes`); `polylines_total` son las polilíneas sugeridas revisadas.
- Higiene de datos: en `notes` solo hechos del proceso (fallos, repeticiones, cotas ausentes), nunca contenido del croquis (lugares, objetos, medidas). Los nombres de archivo siguen el esquema del README.

## Análisis

- G-1: para cada croquis, cociente `minutes_sketch2cad / minutes_freecad`; se informa la mediana y el rango intercuartílico (IQR) del cociente y las medianas de cada método (`python3 validation/compute_g1.py`).
- G-3: porcentaje global y mediana por croquis de polilíneas aceptadas sin cambios, y mediana e IQR del cociente con / sin sugerencias (`python3 validation/compute_g3.py`).
- Se informan como línea base medida, sin decidir aprobado o suspenso; el umbral se fija para v2 con estos datos.

## Límites

- Una sola persona, que además desarrolló la herramienta: conoce sus atajos y sus fallos, lo que favorece a Sketch2CAD frente a FreeCAD.
- Croquis dibujados a propósito, no de casos reales; con ≥ 10 croquis el IQR es orientativo.
- El aprendizaje entre conversiones del mismo croquis solo se reduce con el orden alterno, no se elimina.
- La validación con técnicos queda pendiente; si aparecen, se repite este protocolo con ellos.
