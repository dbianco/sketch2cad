# Spike: lectura de croquis con un modelo de visión (IA externa)

Fecha: 2026-10-27. Estado: experimento, no forma parte del producto. Artefactos en `docs/spikes/ai-extraction/`.

## Pregunta

¿Sirve llamar a una IA con API key para (a) "mejorar" las imágenes antes de nuestro pipeline, o (b) leerlas y devolver datos estructurados? ¿Y se puede generar el CAD directo desde esa llamada?

## Qué se probó

- Dos croquis de ejemplo del propietario (`sketch1.jpg`, papel cuadriculado; `sketch2.jpeg`, a color). El propietario confirmó que no contienen datos de un caso real.
- Modelo `claude-opus-5-5`, esfuerzo `medium`, una llamada por croquis, imagen reducida a 2000 px de lado mayor. Se pidió JSON con: cotas escritas (texto tal cual, valor en metros, a qué se refieren, caja normalizada, legibilidad), estancias, muros, aberturas.
- `extract.py` lee la clave de `ANTHROPIC_API_KEY`; la clave no se imprime ni se guarda.
- Los precios son los de la tabla de la skill `claude-api` (4 USD / 20 USD por millón de tokens de entrada / salida, caché del 2026-10-06): hay que verificarlos antes de usarlos para presupuestar.

## Resultados medidos

| | sketch1.jpg | sketch2.jpeg |
|---|---|---|
| Tiempo | 26,1 s | 17,2 s |
| Tokens entrada / salida | 1125 / 3307 | 892 / 2081 |
| Costo estimado | ~0,07 USD | ~0,045 USD |
| Cotas devueltas | 19 | 10 |
| JSON válido | sí | sí |

**sketch1.jpg.** Las 19 cotas visibles aparecen, con la interpretación correcta de las unidades (coma decimal; "60" sin unidad como 0,60 m; "4 m" como 4,0 m) y con una asociación razonable de cada cota a su elemento. Marcó como dudosas exactamente las que lo son: el "1,2B" sobreescrito (lo leyó 1,26 y anotó que podría ser 1,25 o 1,28) y la cota tachada junto al baño. Las cajas de las cotas caen sobre el texto con error de pocos píxeles (`overlay_sketch1.png`). Los muros salen con error de aproximadamente 1 % de la imagen; las aberturas (puertas y ventanas) están en su sitio.

**sketch2.jpeg.** Las 10 cotas aparecen en las posiciones correctas (borde superior, inferior y lateral derecho). La imagen es de baja resolución y varias cotas son dudosas; el modelo las marcó `uncertain` en 7 de 10 casos. Mi propia lectura difiere en tres valores (el modelo da 4,88, 1,58 y 2,07; yo leo 1,88, 1,68 y 2,00). No tengo verdad de terreno, así que **no puedo decir quién acierta: el propietario debe confirmar esas tres cotas mirando el original.**

Nuestro pipeline determinista, en cambio (misma sesión): con la cuadrícula quitada por tono, `sketch1.jpg` pasa de 239 a 115 polilíneas y los muros quedan casi completos; en `sketch2.jpeg` el ruido son sombreados y rayados que ese filtro no toca.

## Lo que NO se midió

- Exactitud contra una tabla de verdad firmada por el propietario (solo dos croquis; sin verdad de terreno en `sketch2`).
- Comparación con TrOCR sobre estos mismos recortes: no se ejecutó (requiere el extra `ocr`). Referencia anterior: ~75 % de lecturas exactas en números cortos sintéticos.
- Variabilidad entre ejecuciones (una sola llamada por croquis) y otros modelos más baratos.
- Escritura a mano real de otros técnicos, ni imágenes de peor calidad.

## Conclusiones (acotadas a lo medido)

1. **Mejorar la imagen con IA generativa: descartado.** Un modelo que redibuja puede cambiar una cota sin que se note. Sí conviene un preprocesado determinista (filtro de la cuadrícula por tono), gratis y reproducible.
2. **Leer cotas y topología con un modelo de visión es prometedor.** Sobre estos dos croquis lee todas las cotas visibles, entiende unidades y marca las dudosas, y a 0,05 USD por croquis cuesta menos que unos minutos de tiempo humano. Es justo lo que hoy peor hacemos con TrOCR.
3. **Generar el CAD directo no es recomendable.** La geometría del modelo es aproximada (~1 %), no determinista, y sin revisión humana contradice "croquis no es plano a escala". La arquitectura útil es híbrida: el modelo propone cotas y su asociación; el pipeline clásico traza con precisión; la geometría métrica sale de las cotas declaradas, todo como sugerencias `trace_only` que el técnico revisa.
4. **Condición legal y de producto:** enviar croquis de un caso real a un servicio externo contradice la política de la spec (§ 257: `external_ai.enabled=false`, con `authorization_reference` obligatoria y evento de auditoría). Cualquier uso en producto exige esa autorización y marcar `source.method = "model"`.

## Siguiente paso si se decide seguir

Tarea aparte (H7), no incluida aquí: configuración `external_ai.*`, job `model_extract` con registro del modelo y del hash del prompt, sugerencias de cota revisables con el panel por lotes, y una medición con los 10 croquis del set de validación contra la tabla de verdad antes de decidir si reemplaza o complementa a TrOCR.
