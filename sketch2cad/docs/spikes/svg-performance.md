# Spike 1: rendimiento del editor SVG con 2000 entidades

Decisión D-2 (editor en SVG o Konva), criterio H0-10, requisitos REQ-05 y REQ-16.
Fecha de la medición: 2026-10-25. Resultado bruto: `sketch2cad-web/spikes/svg-perf/result.json`.

## Regla de decisión

Se mantiene SVG si la ejecución principal cumple `median_fps >= 60` y `p95_frame_ms <= 25`
en el portátil de desarrollo. Si no lo cumple, el informe recomienda Konva y **H2 no empieza**
hasta migrar el editor.

La ejecución principal se fijó antes de medir: Chromium con ventana (headed) y la técnica
`group-transform` (un `<svg>` cuyo grupo de contenido recibe la transformación de pan y zoom).

## Método

- Página `sketch2cad-web/spikes/svg-perf/index.html`, sin dependencias. Genera datos sintéticos
  deterministas con la semilla fija `20261025` (PRNG mulberry32):
  - 1200 polilíneas de 4 a 12 vértices (tramos de 0,5 a 4 m),
  - 600 líneas (0,5 a 8 m, una de cada cuatro discontinua),
  - 200 símbolos agrupados: marcador de evidencia (círculo + número en `<text>`), puerta
    (línea + arco) y vehículo (rectángulo + dos líneas).
  - En total 2000 entidades sobre un plano de 60 x 40 m en coordenadas de mundo (metros),
    con trazos de 1 a 2 px y `vector-effect: non-scaling-stroke`, como el editor real.
- Recorrido programado de 10 s, continuo y de ida y vuelta: el zoom oscila de forma geométrica
  entre 0,5x y 8x (dos ciclos completos, período 5 s) mientras el centro se desplaza por el plano.
  Se descarta 1 s de calentamiento previo.
- En cada `requestAnimationFrame` se aplica la cámara y se registra el intervalo entre marcas de
  tiempo. `median_fps = 1000 / mediana(intervalo)`; `p95_frame_ms` es el percentil 95 de los
  intervalos. Antes de cada ejecución, 1,5 s de `requestAnimationFrame` en vacío estiman la
  frecuencia de refresco efectiva (`refresh_hz_probe`).
- Técnicas medidas:
  - `group-transform`: atributo `transform="matrix(...)"` en el `<g>` de contenido.
  - `wrapper-css-transform`: el mismo mecanismo que usa `react-zoom-pan-pinch`: el SVG se
    maqueta a zoom 1 y el contenedor recibe `transform: translate(x, y) scale(s)`. La
    dependencia no se añadió; se reproduce su manipulación del DOM.
  - `viewbox`: se cambia el `viewBox` del `<svg>` en cada frame.
- Ejecutor `sketch2cad-web/spikes/svg-perf/run.ts` (`pnpm spike:svg`): sirve la página con un
  servidor HTTP estático de Node en un puerto local libre, abre Chromium de Playwright
  (1.64.1) primero con ventana y después sin ella (headless), viewport 1280 x 800, y escribe
  `result.json`. Se lanza Chromium con los indicadores que evitan la ralentización de ventanas
  ocultas o en segundo plano. El servidor se cierra al terminar.
- Si la técnica principal no cumple en un modo, se ejecuta una vez más con una optimización
  conocida (`will-change: transform` en el grupo y `shape-rendering: optimizeSpeed`), marcada
  como `optimized: true`.

## Hardware

| Campo | Valor |
|---|---|
| Equipo | Apple Mac16,7 (MacBook Pro) |
| CPU | Apple M4 Pro, 14 núcleos |
| GPU | Apple M4 Pro, 20 núcleos, Metal 4 |
| RAM | 24 GB |
| Sistema | macOS 26.5.2 (arm64) |
| Navegador | Chromium 156.0.8078.4 (Playwright 1.64.1) |
| Pantallas | Principal: monitor externo LC24RG50, 1920 x 1080 a 144 Hz; pantalla integrada Liquid Retina XDR en espejo a 120 Hz |
| Refresco medido | 144,9 Hz con ventana; 59,9 Hz headless |

## Resultados

Ejecución registrada en `result.json` (2026-10-25T05:45:43Z), 2000 entidades en todas:

| Modo | Técnica | Optimizada | median_fps | fps medio | p95_frame_ms | p99_frame_ms | máx. ms | frames > 25 ms | frames | duración s |
|---|---|---|---|---|---|---|---|---|---|---|
| **headed** | **group-transform (principal)** | no | **144,93** | 142,49 | **8,3** | 8,7 | 63,1 | 1 | 1426 | 10,008 |
| headed | wrapper-css-transform | no | 144,93 | 142,08 | 8,3 | 8,7 | 47,8 | 2 | 1422 | 10,008 |
| headed | viewbox | no | 144,93 | 141,88 | 8,4 | 8,7 | 63,9 | 2 | 1420 | 10,008 |
| headless | group-transform | no | 59,88 | 60,00 | 16,7 | 16,8 | 16,8 | 0 | 601 | 10,016 |
| headless | wrapper-css-transform | no | 59,88 | 60,00 | 16,8 | 16,8 | 16,8 | 0 | 602 | 10,033 |
| headless | viewbox | no | 59,88 | 60,00 | 16,8 | 16,8 | 16,8 | 0 | 601 | 10,016 |
| headless | group-transform | sí | 59,88 | 60,00 | 16,7 | 16,8 | 16,8 | 0 | 601 | 10,016 |

Repetibilidad: dos ejecuciones anteriores del mismo script ese día dieron con ventana
`median_fps` 144,93 en las tres técnicas, `p95_frame_ms` entre 8,3 y 8,6 y entre 0 y 4 frames
por encima de 25 ms por ejecución.

La fila headless optimizada aparece porque, headless, la técnica principal da 59,88 < 60: ese
valor es el tope del temporizador de frames headless (16,7 ms fijos), no coste de render, y la
optimización no lo cambia. La regla se aplica a la ejecución principal con ventana.

## Decisión

Ejecución principal (headed, `group-transform`): `median_fps` = 144,93 (>= 60) y
`p95_frame_ms` = 8,3 (<= 25). **Se cumple la regla: se mantiene SVG para el editor** y H2
puede empezar. No se recomienda Konva.

## Limitaciones

- `median_fps` no puede superar la frecuencia de refresco: la pantalla principal va a 144 Hz,
  así que 144,93 fps es el tope y las tres técnicas lo alcanzan. La medición demuestra que hay
  margen (mediana 6,9 ms frente a un presupuesto de 16,7 ms a 60 Hz), pero no cuantifica cuánto.
  En una pantalla de 60 Hz el máximo sería unos 60 fps, y una mediana de 59,9 por redondeo del
  intervalo podría quedar justo bajo el umbral aunque no se pierda ningún frame.
- Headless, Chromium entrega frames con un temporizador fijo a 60 Hz sin composición real por
  GPU; esas filas no miden el coste de render y no sirven para decidir.
- El equipo de desarrollo (M4 Pro) es bastante más potente que un portátil medio; el requisito
  de 60 fps "en un portátil medio" de la NFR solo queda verificado en este equipo, como acordó el
  dueño. Conviene repetir `pnpm spike:svg` en un portátil modesto antes de H2 si hay uno a mano.
- Solo se mide pan y zoom. No se miden selección, arrastre de vértices, edición ni el coste de
  React/Zustand reconciliando 2000 elementos; eso queda para las pruebas del editor.
- Los picos aislados (de 0 a 4 frames por encima de 25 ms por ejecución, el peor de 139 ms)
  aparecen en todas las técnicas y no se analizaron; no afectan al p95.

## Recomendación de técnica para el editor

En rendimiento las tres técnicas empatan en este equipo. El criterio que las separa es la
corrección visual, comprobada con capturas a zoom 8x:

- `wrapper-css-transform` (el mecanismo por defecto de `react-zoom-pan-pinch`) **rompe
  `vector-effect: non-scaling-stroke`**: el escalado CSS del contenedor no lo compensa el
  navegador y a 8x los trazos de 1 px se ven de unos 8 px. No sirve para el lienzo del editor.
- `group-transform` y `viewbox` se ven idénticos y mantienen el grosor de trazo constante.

Se recomienda **`viewbox`** para el editor: mismo rendimiento, trazos correctos, la cámara es un
único atributo (`x y ancho alto` en metros), las coordenadas del puntero se convierten con
`getScreenCTM()` sin álgebra adicional y no añade dependencias. `group-transform` es una
alternativa equivalente. `react-zoom-pan-pinch` puede seguir usándose para el visor de la fuente
(una imagen raster, donde escalar con CSS es correcto) o solo para gestionar gestos que
alimenten la cámara del `viewBox`, pero no para transformar el SVG del editor.
