# Suposiciones de PoC a revisar

Decisiones tomadas para la prueba de concepto que deben revisarse al probar con croquis y usuarios reales. Cada entrada indica dónde está documentada o implementada.

- **Vértices de muros como referencias métricas (D-15, 2026-10-26).** `triangulate` y `baseline_offset` aceptan como referencia un vértice `{entity_id, vertex}` de una línea o polilínea con `metric_origin` métrico (longitudes declaradas o dibujo a escala métrico); si no lo es, V-07. Ver la nota de PoC de §11.3 de la spec y D-15 del jotdown. A revisar al probar con croquis reales.
  - La web permite elegir esquinas de líneas como referencia (ConstraintTool).
- **Plano calibrado vacío.** Un plano calibrado sin entidades queda `approximate` (regla de 001-057; pendiente de confirmación del dueño).
- **Fijar un segmento de una polilínea `scaled_drawing`.** Declarar la longitud de un solo segmento la degrada a `trace_only` hasta que se declaren todos (001-072).
- **Símbolos `paper_mm` a tamaño fijo en pantalla.** Se dibujan con 1 mm de papel = 1 mm CSS, sin seguir el zoom.
- **Deshacer no cruza operaciones métricas del servidor.** Fijar longitud, triangular y base+offset vacían la pila de deshacer local (001-079).
- **403 para quien no es miembro.** Un usuario que no es miembro del expediente recibe 403 en lugar de 404 (revela que el recurso existe).
- **Puntos dibujados a mano nacen `none`.** En un croquis esquemático un punto o símbolo dibujado a mano tiene `metric_origin` `none` hasta fijarlo por triangulación o base+offset.
- **Herramienta Cota (tecla A).** Crea una cota declarada (`dimension` en `L_DIMENSIONS`) con dos clics o con un clic sobre un segmento; la separación por defecto de la línea de cota es 5 mm de papel (0,50 m a 1:100). Se deshace con Deshacer como cualquier entidad dibujada. La tecla C no se usó porque cierra la polilínea.
  - El lienzo marca la discrepancia entre valor declarado y medido con la tolerancia por defecto de `dimension.discrepancy_tolerance_m` (0,02 m); un override por tipo de incidente solo se refleja en el panel de estado métrico, que usa el cálculo del servidor.
  - Una cota cuyos dos puntos coinciden se rechaza con 422 al crearla o editarla (la exportación DXF/PDF no puede dibujarla).
