# H2-12: rendimiento del editor real con 2000 entidades

Tarea 001-068, requisito REQ-05. Repite el spike 1 (`docs/spikes/svg-performance.md`) contra el
`EditorCanvas` real en lugar de la página sin dependencias.
Fecha de la medición: 2026-10-25. Resultado bruto: `sketch2cad-web/spikes/svg-perf/editor-result.json`.

## Regla de decisión

Se mantiene SVG si la ejecución principal cumple `median_fps >= 60` (o está en el tope de
refresco de la pantalla, ver más abajo) y `p95_frame_ms <= 25` en el portátil de desarrollo. Si
no lo cumple, el informe recomienda Konva y **H3 queda BLOQUEADO** hasta migrar el editor.

La ejecución principal se fijó antes de medir: Chromium con ventana (headed), escenario de pan y
zoom (`pan-zoom`) con el recorrido del spike 1.

### Lectura "en el tope de refresco"

`median_fps` no puede superar la frecuencia de refresco de la pantalla: el navegador entrega un
`requestAnimationFrame` por refresco. Por eso:

- En una pantalla de 120 o 144 Hz el umbral de 60 fps se cumple con holgura si no se pierden
  frames (la mediana sale en ~120 o ~144,9 fps).
- En una pantalla de 60 Hz la mediana sin ningún frame perdido es ~59,9 fps (intervalo de
  16,7 ms redondeado), que leído al pie de la letra queda bajo 60. Se considera que cumple si la
  mediana está a menos de un 2 % de `refresh_hz_probe` (`refresh_cap_tolerance` = 0,02 en el
  JSON): eso significa que la mediana de los frames entra en el presupuesto del refresco. Un
  frame perdido a 60 Hz duplica el intervalo (33,3 ms), así que la tolerancia no oculta pérdidas.
- El JSON guarda las dos lecturas: `passes_strict` (regla literal) y `passes` (regla con la
  lectura en el tope). La decisión usa `passes`. En esta medición coinciden.

## Método

- Página `sketch2cad-web/spikes/svg-perf/editor.html` + `editor-entry.tsx`, que monta el
  `EditorCanvas` real **sin modificar** (sin ganchos de prueba), con un store propio
  (`createEditorStore()`) cargado con `load(revisionId, entidades)`, el catálogo real
  `src/editor/fixtures/catalog-default-1.json` y tamaño fijo igual a la ventana (1280 x 800).
- Datos sintéticos con el mismo generador que el spike 1 (mulberry32, semilla `20261025`, misma
  secuencia aleatoria) y la misma mezcla: 1200 polilíneas abiertas de 4 a 12 vértices
  (`L_STRUCTURE`), 600 líneas de 0,5 a 8 m (una de cada tres en `L_ANNOTATIONS`, el resto en
  `L_STRUCTURE`) y 200 símbolos reales del catálogo: `evidence_marker` (`paper_mm`, con
  `evidence_number`), `door` y `generic_vehicle` (`world_m`), cada uno en su capa por defecto.
  Plano de 60 x 40 m en metros. El editor dibuja todo con su trazo real (1,5 px,
  `vector-effect: non-scaling-stroke`) y su cámara real por `viewBox`.
- La cámara se mueve por las entradas reales del lienzo, no por un gancho:
  - zoom: un evento `wheel` por frame en el centro del lienzo, con el `deltaY` que da el factor
    de zoom pedido (`zoomAt` mantiene el centro);
  - pan: un arrastre con el botón central (`pointerdown` al empezar y un `pointermove` por
    frame), que el lienzo convierte en `pan`.
  El arnés lee la cámara confirmada del atributo `viewBox` del `<svg>`.
- Escenarios (cada uno en una página nueva):
  - `pan-zoom` (principal): recorrido programado de 10 s del spike 1 (zoom geométrico entre 0,5x
    y 8x, dos ciclos de 5 s, centro desplazándose por el plano; zoom 1x = 20 px/m), con 1 s de
    calentamiento descartado.
  - `select-move-vertex`: 5 s (más 1 s de calentamiento) en los que cada frame selecciona una
    polilínea distinta y confirma un `moveVertex` sobre ella (actualización Immer del store,
    entrada de historial, operación en la cola de sincronización y re-render). Es más exigente
    que un arrastre real, que solo confirma una entrada al soltar.
  - montaje inicial: en cada página se mide `load` del store, el primer render y commit de React
    (`flushSync`) y el tiempo hasta el primer frame pintado.
- En cada `requestAnimationFrame` se registra el intervalo entre marcas de tiempo, igual que el
  spike 1: `median_fps = 1000 / mediana(intervalo)`; `p95_frame_ms` es el percentil 95. Antes de
  cada escenario, 1,5 s de `requestAnimationFrame` en vacío dan `refresh_hz_probe`. También se
  guarda `work_per_frame_ms`: el trabajo síncrono del frame (eventos + render + commit de React),
  sin la pintura.
- Ejecutor `sketch2cad-web/spikes/svg-perf/editor-run.ts` (`pnpm spike:editor`): compila la
  página con Vite en modo producción (React de producción, como la app) en una carpeta temporal,
  la sirve con un servidor HTTP estático de Node en un puerto local libre, abre Chromium de
  Playwright (1.64.1) primero con ventana y después headless, viewport 1280 x 800, con los mismos
  indicadores que el spike 1 contra la ralentización de ventanas ocultas, y escribe
  `editor-result.json` (mismo esquema que `result.json` más `scenario`, `scenarios`,
  `passes_strict`, `refresh_cap_tolerance` y `mount`). El código del arnés no entra en
  `pnpm build` (la app solo compila `index.html` de la raíz).
- Modo rápido para CI: `pnpm spike:editor --smoke` ejecuta solo headless, con recorridos de
  1,5 s y 0,5 s de calentamiento, y escribe en la carpeta temporal del sistema
  (`editor-result.smoke.json`), sin tocar `editor-result.json`. Solo comprueba que el arnés
  funciona de principio a fin; sus números no son una medición.

## Hardware y navegador

| Campo | Valor |
|---|---|
| Equipo | Apple Mac16,7 (MacBook Pro) |
| CPU | Apple M4 Pro, 14 núcleos |
| GPU | Apple M4 Pro, 20 núcleos |
| RAM | 24 GB |
| Sistema | macOS 26.5.2 (arm64) |
| Navegador | Chromium 156.0.8078.4 (Playwright 1.64.1) |
| Pantallas | Principal: monitor externo LC24RG50, 1920 x 1080 a 144 Hz; pantalla integrada (Color LCD) a 120 Hz |
| Refresco medido | 144,9 Hz con ventana; 59,9 Hz headless |
| Viewport | 1280 x 800, devicePixelRatio 1 |

## Resultados

Ejecución registrada en `editor-result.json` (2026-10-25T19:25:02Z), 2000 entidades montadas
(2000 elementos `data-entity-id` en el DOM) en todas:

| Modo | Escenario | median_fps | fps medio | p95_frame_ms | p99_frame_ms | máx. ms | frames > 25 ms | frames | duración s | trabajo/frame mediana / p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| **headed** | **pan-zoom (principal)** | **144,93** | 143,98 | **8,3** | 8,6 | 8,7 | 0 | 1441 | 10,008 | 1,2 / 1,5 |
| headed | select-move-vertex | 144,93 | 143,77 | 8,5 | 8,7 | 14,7 | 0 | 719 | 5,001 | 1,8 / 2,1 |
| headless | pan-zoom | 59,88 | 60,00 | 16,7 | 16,8 | 16,8 | 0 | 602 | 10,033 | 1,2 / 1,8 |
| headless | select-move-vertex | 59,88 | 60,00 | 16,8 | 16,8 | 16,8 | 0 | 301 | 5,016 | 1,8 / 2,3 |

Montaje inicial (2000 entidades):

| Modo | Escenario de la página | `load` del store ms | render + commit ms | hasta el primer frame pintado ms |
|---|---|---|---|---|
| headed | pan-zoom | 5,0 | 24,7 | 47,2 |
| headed | select-move-vertex | 4,8 | 18,2 | 37,7 |
| headless | pan-zoom | 4,7 | 34,1 | 67,3 |
| headless | select-move-vertex | 3,7 | 16,3 | 33,2 |

Repetibilidad: una ejecución completa anterior ese mismo día (durante la puesta a punto del
arnés, con la misma página salvo la lectura de la cámara) dio con ventana `median_fps` 144,93 en
los dos escenarios, `p95_frame_ms` 8,4 (pan-zoom) y 8,3 (select-move-vertex), sin frames por
encima de 25 ms, y un primer frame pintado de 73,7 ms.

Headless, Chromium entrega frames con un temporizador fijo a 60 Hz (16,7 ms): las filas headless
están en el tope de refresco (59,88 frente a `refresh_hz_probe` 59,9), no miden coste de render y
no deciden.

## Decisión

Ejecución principal (headed, `pan-zoom`): `median_fps` = 144,93 (>= 60, y además en el tope de
la pantalla de 144 Hz) y `p95_frame_ms` = 8,3 (<= 25). `passes_strict` y `passes` son `true`.
**Se cumple la regla: se mantiene SVG para el editor** y H3 no queda bloqueado. No se recomienda
Konva.

El escenario de edición también cumple (144,93 fps, p95 8,5 ms) y el montaje de 2000 entidades
queda por debajo de 75 ms hasta el primer frame pintado.

## Limitaciones

- Como en el spike 1, la mediana está en el tope de refresco de 144 Hz: demuestra que no se
  pierden frames, no cuánto margen queda. `work_per_frame_ms` lo acota en parte: entre 1,2 y
  2,3 ms de trabajo síncrono por frame frente a un presupuesto de 16,7 ms a 60 Hz, pero no
  incluye estilo, maquetación ni pintura del navegador.
- El pan llega por `pointermove`, que React programa con prioridad continua: se renderiza en la
  tarea siguiente al frame, igual que con un ratón real, así que la cámara va un frame por
  detrás del recorrido (`max_centre_tracking_error_m` 0,36 m a 144 Hz y 0,71 m a 60 Hz, el
  desplazamiento de un frame). El coste de ese render sí cae dentro de cada intervalo medido,
  pero no dentro de `work_per_frame_ms` del escenario `pan-zoom`. El zoom (`wheel`) se confirma
  en el mismo frame (`max_zoom_tracking_error` ~2e-16).
- El equipo (M4 Pro) es bastante más potente que un portátil medio. Conviene repetir
  `pnpm spike:editor` en un portátil modesto y con pantalla de 60 Hz; allí se aplicará la lectura
  en el tope de refresco descrita arriba.
- No se mide la imagen de origen bajo las entidades, ni herramientas activas con vista previa, ni
  el arrastre real con `VertexEditTool` (vista previa por frame y una sola confirmación al
  soltar); `select-move-vertex` confirma en cada frame, que es el caso más caro para el store.
- El historial está limitado a 200 entradas, así que el escenario de edición mide el régimen
  estable con el historial lleno.
- CI no ejecuta todavía `pnpm spike:editor --smoke`: requiere instalar el Chromium de Playwright
  en el job (`pnpm exec playwright install chromium`).
