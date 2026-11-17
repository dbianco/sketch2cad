# Conjunto de validación interna (H6)

Material para medir G-1, G-2 y G-3 (REQ-20, jotdown H6-5 y H6-6). Es una **medición interna**: la hace el desarrollador con croquis dibujados a propósito, no es una validación con técnicos planimetristas, que sigue pendiente. El informe final (`docs/validation/report.md`, tarea 001-121) debe decirlo así.

## Estructura

| Ruta | Contenido |
|---|---|
| `sketches/` | ≥ 10 croquis dibujados a mano y fotografiados o escaneados (los aporta el dueño). |
| `pattern/` | ≥ 1 foto oblicua del patrón impreso de dimensiones conocidas (la aporta el dueño). |
| `synthetic/generate.py` | Generador del conjunto sintético con verdad conocida; su salida (`synthetic/output/`) no se versiona. |
| `measure_protocol.md` | Protocolo de cronometraje de G-1 y G-3. |
| `timings.csv` | G-1: una fila por croquis con los dos tiempos. |
| `dimension_check.csv` | G-2: una fila por cota declarada, revisada por una segunda persona. |
| `suggestions.csv` | G-3: aceptación de sugerencias y tiempos con y sin sugerencias. |
| `compute_g1.py`, `compute_g2.py`, `compute_g3.py` | Cálculo de cada objetivo a partir de su CSV. |
| `query_suggestion_acceptance.py` | Cuenta, desde los eventos, las polilíneas sugeridas aceptadas sin cambios. |

## Reglas

- **Ningún dato real de expedientes**: ni croquis de casos, ni direcciones, matrículas, nombres o fechas reales. Las escenas son inventadas (una habitación, un cruce, un tramo de ruta genérico).
- **Ningún dato personal**: nada de nombres de personas en los croquis ni en los CSV (en `person` y `# reviewer:` van iniciales).
- **Nombres de archivo**: `croquis-validacion-NN.<ext>` para los croquis (`NN` = 01, 02, …; `<ext>` = `jpg`, `png`, `pdf`…) y `croquis-validacion-patron-NN.<ext>` para las fotos del patrón. Ambos coinciden con el glob `croquis-validacion-*` de `[names] globs` en `sketch2cad-api/scan_logs_patterns.toml` (y las rutas, con `validation/sketches/*` y `validation/pattern/*`): `scripts/scan_logs.py` marca esos nombres, con o sin extensión, si aparecen en un log (comprobado con el escáner real). El nombre no describe el contenido (nada de `croquis-cruce-ruta-9`).
- **EXIF y GPS**: antes de copiar una foto aquí, quitar sus metadatos, por ejemplo con `exiftool -all= -overwrite_original croquis-validacion-01.jpg` (o exportando de nuevo sin metadatos) y comprobar con `exiftool croquis-validacion-01.jpg` que no queda `GPS*` ni el modelo de cámara.
- Los CSV versionados son solo cabeceras: ningún dato inventado. Las columnas no admiten texto del contenido del croquis en `notes` (ver el protocolo).
- Secreto `VALIDATION_NAMES` de GitHub: un nombre por línea, con y sin extensión (`croquis-validacion-01.jpg` y `croquis-validacion-01`), para que el escaneo de logs de CI (H6-8) los busque también como literales (ver `docs/log-hygiene.md`).

## Croquis a mano: cotas declaradas (H6-5)

Al menos 10 croquis, repartidos entre las tres prácticas de campo: croquis esquemático con cotas (`schematic`), triangulación desde dos esquinas fijas (`triangulation`) y línea base + offset sobre una vía (`baseline_offset`). Cada cota escrita en el papel es una fila; `dimension_id` es la etiqueta que se le da al pasarla (d1, d2…) y es la misma que se usa en `dimension_check.csv`. El valor se copia tal como está escrito, con sus decimales.

| Archivo | Práctica | Cota | Qué mide | Valor declarado (m) | Texto escrito |
|---|---|---|---|---|---|
| *EJEMPLO, no es un dato:* `croquis-validacion-00.jpg` | schematic | d1 | pared inferior, esquina A a esquina B | 4,20 | `4,20` |
| | | | | | |

## Patrón impreso (CA-02)

Al menos una foto oblicua (≈ 30-45° respecto de la perpendicular, la hoja entera dentro del encuadre) de un patrón impreso a escala 1:1 cuyas dimensiones se midieron con regla sobre el papel impreso (la impresora puede escalar). Sirve para calibrar con 4 puntos y comprobar que, tras rectificar, las distancias quedan dentro de la tolerancia.

| Archivo | Elemento | Dimensión nominal (mm) | Dimensión medida con regla (mm) |
|---|---|---|---|
| *EJEMPLO, no es un dato:* `croquis-validacion-patron-00.jpg` | rectángulo exterior, ancho | 250 | 249,5 |
| | | | |

El generador sintético dibuja un patrón de referencia (rectángulo de 250 × 160 mm, puntos cada 50 mm y una barra de 100 mm) que puede servir de modelo para imprimir.

## Conjunto sintético

Desde la raíz del repositorio, con el entorno de la API (usa `sketch2cad`, NumPy y OpenCV):

```
uv run --project sketch2cad-api python validation/synthetic/generate.py --count 20 [--out DIR] [--seed N] [--grid mixed|on|off] [--font plain|simplex|script]
```

o, con el entorno activado (`source sketch2cad-api/.venv/bin/activate`), el comando literal del criterio: `python validation/synthetic/generate.py --count 20`. El Python del sistema sin esas bibliotecas no sirve: el script lo dice y sale con código 1. Unos 4 s para 20 imágenes (≈ 22 MB) en un M4 Pro.

- La imagen `i` es de tipo `schematic`, `triangulation`, `baseline_offset` u `oblique_pattern`, en ese ciclo: 5 de cada uno con `--count 20`. Las tres primeras son hojas A4 apaisadas ya rectificadas (10 px por mm de papel, como la entrada de `suggest_lines`); con `--grid mixed` (por defecto) la mitad lleva cuadrícula naranja clara de 5 mm, como el papel de los croquis reales. La cuarta es la foto oblicua (`warpPerspective` con homografía conocida) del patrón impreso sobre una mesa.
- Aspecto a mano, con límites: trazo con temblor gaussiano, grosor y tono variables, vértices desplazados (líneas no del todo ortogonales), proporciones no a escala en los esquemáticos, texto rotado y algo inclinado. Las cifras usan fuentes Hershey de OpenCV; las tres opciones se leen bien a la vista y se usa `plain` por defecto porque es la que TrOCR leyó con la coma correcta en el fixture de E2E (`sketch2cad-web/e2e/fixtures/make_dimensioned_sketch.py`). Con este generador no se probó el OCR (requiere el extra `ocr`).
- **Determinista**: la imagen `i` solo usa `numpy.random.default_rng([seed, i])`; la misma semilla da los mismos bytes en dos ejecuciones en la misma máquina y entorno (comprobado con `diff -r`). Otras plataformas o versiones de NumPy/OpenCV pueden variar algún píxel.
- Cada `NN-<tipo>.jpg` tiene su `NN-<tipo>.json` (esquema `sketch2cad-validation-synthetic/1`) e `index.json` los lista. Coordenadas: papel en mm e imagen en px con Y hacia abajo; mundo en metros con Y hacia arriba, origen en `A` y eje X de `A` a `B`.
  - `points[]`: `id`, `world_m`, `paper_mm`, `image_px`.
  - `segments[]`: `id`, `role` (`wall`, `measure`, `baseline`, `road_edge`, `offset`, `pattern`), extremos en `paper_mm` y `image_px`.
  - `dimensions[]`: `id`, `text` (como está escrito, `4,20`), `value_m`, `points` (los dos puntos que mide), `segment_id` (o `null` si no hay trazo propio) y `text_box_px`.
  - `evidence[]`: `label`; para `triangulation`, `refs` [A, B], `d_a_m`, `d_b_m`, `side`; para `baseline_offset`, `refs`, `s_m`, `d_m`, `side`; y `expected_world_m`, el punto que debe dar la herramienta con los valores escritos (redondeados al centímetro).
  - `oblique_pattern`: `homography` (`paper_mm_to_image_px` e `image_px_to_paper_mm`) y `pattern` (hoja, rectángulo, puntos y barra en mm y px, con sus dimensiones conocidas).
- Prueba: `sketch2cad-api/tests/validation/test_validation_synthetic.py` (cantidad, esquema, determinismo, coordenadas esperadas contra la geometría del producto, puntos del patrón dentro de 1 mm tras rectificar y paso por `suggest_polylines` y QC).

## Mediciones

- **G-1** (tarea 001-116): seguir `measure_protocol.md`, anotar en `timings.csv` y calcular con `python3 validation/compute_g1.py` (mediana e IQR del cociente Sketch2CAD / FreeCAD; línea base, sin umbral).
- **G-2** (tarea 001-117): la segunda persona pone sus iniciales en la primera línea de `dimension_check.csv` (`# reviewer: AB`; el script se niega a calcular sin ellas) y una fila por cota. `exported_value_m` es el texto de la cota en el DXF exportado; FreeCAD no muestra ese texto (muestra su propia medida), así que se lee con `uv run --project sketch2cad-api python -c "import ezdxf,sys; [print(d.dxf.text) for d in ezdxf.readfile(sys.argv[1]).modelspace().query('DIMENSION')]" plan.dxf` o en el PDF. Para un segmento fijado con «fijar longitud» se usa la longitud del segmento en el DXF. **Coincidencia**: el valor exportado, redondeado (mitad hacia arriba) a los decimales del valor escrito, es igual al escrito; si el exportado tiene menos decimales que el escrito, deben ser iguales numéricamente (un decimal perdido no coincide). La columna `match` (`yes`/`no`) es el juicio del revisor y el script lista las filas donde no coincide con la regla. `python3 validation/compute_g2.py` imprime el porcentaje y si alcanza el objetivo ≥ 99 %.
- **G-3** (tarea 001-118): para el plano de cada croquis convertido con sugerencias, `DATABASE_URL=… uv run --project sketch2cad-api python validation/query_suggestion_acceptance.py --plan-id <uuid> --sketch croquis-validacion-NN.jpg` cuenta, desde los eventos `suggestions.added`, `entity.reviewed`, `entity.updated` y `entity.deleted`, las polilíneas aceptadas sin cambios, corregidas, rechazadas, retiradas tras revisarlas, pendientes y descartadas por recalibración; imprime el inicio de la fila para `suggestions.csv`. «Aceptada sin cambios» = aceptada y nunca editada, ni antes ni después. El porcentaje se calcula sobre las revisadas. Medir antes de reabrir la revisión (las copias de una reapertura tienen ids nuevos). `python3 validation/compute_g3.py` da el porcentaje global, la mediana por croquis y el cociente de tiempos con / sin sugerencias.
- **«H5 no construido»**: si H5 se hubiera recortado, `suggestions.csv` empezaría con la línea `# H5 no construido` y el informe marcaría CA-04, CA-05 y G-3 como validación posterior (`compute_g3.py` lo reconoce). **No aplica**: H5 está construido.
- Los scripts de cálculo solo usan la biblioteca estándar (funcionan también con el `python3` del sistema) y nombran filas por número, nunca por archivo de croquis.

## Estado

| Archivo | Tarea | Estado |
|---|---|---|
| `README.md`, `sketches/`, `pattern/` | 001-115 | Andamiaje listo; faltan los croquis, la foto del patrón y las tablas. |
| `synthetic/generate.py` | 001-115 | Listo (se genera bajo demanda; no se versiona la salida). |
| `measure_protocol.md`, `timings.csv`, `compute_g1.py` | 001-116 | Andamiaje listo; faltan los cronometrajes. |
| `dimension_check.csv`, `compute_g2.py` | 001-117 | Andamiaje listo; faltan la revisión y las iniciales del revisor. |
| `suggestions.csv`, `compute_g3.py`, `query_suggestion_acceptance.py` | 001-118 | Andamiaje listo; faltan las conversiones y las consultas. |

## Pendiente del dueño (001-115)

- [ ] Dibujar a mano ≥ 10 croquis siguiendo la práctica de campo (esquemáticos con cotas, triangulaciones desde dos esquinas, línea base + offset en una vía), con escenas inventadas.
- [ ] Fotografiarlos o escanearlos, quitar EXIF/GPS y guardarlos como `sketches/croquis-validacion-NN.<ext>`.
- [ ] Imprimir el patrón, medirlo con regla, fotografiarlo en oblicuo y guardarlo como `pattern/croquis-validacion-patron-NN.<ext>`.
- [ ] Completar las dos tablas de este README (borrar las filas de ejemplo).
- [ ] Generar el conjunto sintético (`--count 20`) y comprobar que se crea.
- [ ] Crear el secreto `VALIDATION_NAMES` con los nombres reales, con y sin extensión.
