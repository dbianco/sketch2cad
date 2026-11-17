# Auditoría final de licencias y dependencias (CA-13, H6-4)

Fecha: 2026-10-26. Tarea 001-114. Rama `ai/001-114-final-license-audit` sobre el commit `1cb6b25`.

Política aplicada (DEC-6, ampliada por el dueño el 2026-10-25): se acepta cualquier licencia salvo GPL y AGPL, que están prohibidas y no admiten excepción. Las licencias que no están en la lista blanca (`sketch2cad-api/licenses-allowed.txt`, `sketch2cad-web/licenses-allowed.json`) necesitan una excepción documentada con motivo. Pillow (MIT-CMU) y pypdfium2 fueron aprobados expresamente por el dueño.

## Herramientas y versiones

| Herramienta | Versión | Uso |
|---|---|---|
| uv | 0.12.5 | `uv sync --frozen` sobre `sketch2cad-api/uv.lock` |
| Python | 3.12.15 | entorno por defecto y entorno con el extra `ocr` |
| pip-licenses | 5.5.5 | lo invoca `scripts/check_licenses.py` (`--from=mixed --with-system`) |
| pip-audit | 2.10.1 | vulnerabilidades del entorno Python; veredicto de `scripts/check_audit.py` |
| node | 26.12.1 (local; CI usa 24.22.1) | `scripts/check-licenses.mjs` |
| pnpm | 11.20.0 | `pnpm install --frozen-lockfile`, `pnpm audit` |
| license-checker-rseidelsohn | 5.0.1 | lo invoca `scripts/check-licenses.mjs` |

## Método

1. API, entorno por defecto (desde `sketch2cad-api/`): `uv sync --frozen`, `uv run python scripts/check_licenses.py`, `uv run pip-audit --format json --progress-spinner off | uv run python scripts/check_audit.py`.
2. API con el extra `ocr` (torch, transformers): entorno aparte con `UV_PROJECT_ENVIRONMENT=<venv> uv sync --frozen --extra ocr` y los mismos comandos con `uv run --frozen --extra ocr`, pasando los dos archivos de excepciones: `--exceptions license-exceptions.toml --exceptions license-exceptions-ocr.toml`.
3. Web (desde `sketch2cad-web/`): `pnpm install --frozen-lockfile`, `node scripts/check-licenses.mjs`, `pnpm audit --audit-level=high` y `pnpm audit --audit-level=high --json | node scripts/check-audit.mjs`.
4. Las licencias de torch, transformers y regex se leyeron de `METADATA` y de los archivos `licenses/` de cada `dist-info` instalado. Las bibliotecas nativas que traen dentro las ruedas de torch y opencv se revisaron listando el contenido de las ruedas y leyendo la cadena de licencia que FFmpeg compila en `libavcodec`/`libavutil`.
5. La resolución en Linux se revisó con `uv.lock` y descargando dos ruedas de Linux x86_64 (torch `2.14.1+cpu` y opencv-python-headless `5.0.0.93`) cuyo SHA-256 coincide con el del lock. Esas ruedas se inspeccionaron como archivos zip, sin instalarlas.

### Excepciones del extra `ocr` en un archivo aparte

El verificador falla con las excepciones que no corresponden a ningún paquete instalado (excepción obsoleta). Por eso las excepciones de torch, transformers y regex están en `sketch2cad-api/license-exceptions-ocr.toml` y no en `license-exceptions.toml`:

- entorno por defecto: solo `license-exceptions.toml` (comportamiento sin cambios, job `licenses`);
- entorno con `--extra ocr`: los dos archivos (`--exceptions` se puede repetir), job `licenses-ocr` de `api-ci.yml`, que además corre pip-audit con el extra.

Comprobado: el entorno por defecto con los dos archivos falla con 3 excepciones obsoletas (torch, transformers, regex), y el entorno `ocr` con solo el archivo por defecto falla con esos 3 paquetes sin excepción. Las dos combinaciones correctas pasan.

## Resultados

| Ecosistema | Paquetes revisados | Licencias | Avisos | Vulnerabilidades |
|---|---|---|---|---|
| API, entorno por defecto | 91 (pip-licenses, sin el propio `sketch2cad`); pip-audit: 92 dependencias | pasa (exit 0) | 0 | 0 hallazgos; `sketch2cad` omitido por no estar en PyPI |
| API, extra `ocr` (macOS arm64) | 107; pip-audit: 108 dependencias | pasa (exit 0) con los dos archivos | 1: torch `pending-owner-decision` | 0 hallazgos |
| Web | 377 (dependencias de producción y desarrollo instaladas, sin el propio `sketch2cad-web`) | pasa (exit 0) | 0 | `pnpm audit --audit-level=high`: "No known vulnerabilities found"; `check-audit.mjs`: 0 advisories |

El extra `ocr` añade 16 paquetes al entorno por defecto: fsspec, hf-xet, huggingface_hub, Jinja2, mpmath, PyYAML, regex, safetensors, setuptools, shellingham, sympy, tokenizers, torch, tqdm, transformers y typer.

### Tabla de excepciones

API, `sketch2cad-api/license-exceptions.toml` (entorno por defecto):

| Paquete | Versión | Licencia | Motivo (resumen) | Estado |
|---|---|---|---|---|
| psycopg | * | LGPL-3.0-only | Driver de PostgreSQL que exige Procrastinate (D-6, DEC-6); se usa sin modificar | approved |
| psycopg-binary | * | LGPL-3.0-only | Build binario de psycopg (`psycopg[binary]`) | approved |
| psycopg-pool | * | LGPL-3.0-only | Pool de conexiones de psycopg que usa Procrastinate | approved |
| pillow | * | MIT-CMU | Lectura de imágenes, DPI y EXIF en la ingesta; permisiva, aprobada por el dueño | approved |
| pypdfium2 | * | BSD-3-Clause, Apache-2.0, dependency licenses | Rasterizador de PDF de la spec; PDFium y sus dependencias con licencias permisivas | approved |

API, `sketch2cad-api/license-exceptions-ocr.toml` (solo con `--extra ocr`):

| Paquete | Versión | Licencia | Motivo (resumen) | Estado |
|---|---|---|---|---|
| torch | * (2.14.1 en macOS/Windows, 2.14.1+cpu en Linux) | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT | Todas las licencias declaradas son permisivas (LICENSE de PyTorch estilo BSD-3-Clause; BSL-1.0 es la Boost Software License de `third_party/sleef`). Pendiente solo por `libgomp.so.1` dentro de la rueda de Linux (ver decisiones abiertas) | **pending-owner-decision** |
| transformers | * | Apache 2.0 License | Grafía no SPDX en `METADATA`; `licenses/LICENSE` es el texto completo de Apache License 2.0 | approved |
| regex | * | Apache-2.0 AND CNRI-Python | `licenses/LICENSE.txt`: deriva del módulo `re` de CPython 2.6/3.1 bajo la licencia CNRI de Python 1.6; los añadidos son Apache 2.0. Ambas permisivas | approved |

Web, `sketch2cad-web/license-exceptions.json`: sin entradas (`"exceptions": []`). Tras ampliar la lista blanca el 2026-10-25 (ISC, BlueOak-1.0.0, MIT-0, 0BSD, Zlib, CC0-1.0, CC-BY-3.0/4.0, MPL-2.0) no hace falta ninguna.

Las antiguas excepciones pendientes de cffi (MIT-0), certifi (MPL-2.0) y numpy (expresión compuesta) desaparecieron con esa ampliación: sus licencias ya están en la lista blanca.

Todas las entradas tienen `package`, `version`, `license`, `reason` no vacío y `status`. Lo comprueba `tests/test_check_licenses.py::test_committed_exception_files_are_complete` sobre los dos archivos TOML.

### Dependencias transitivas de torch, transformers y opencv

Leídas del entorno `ocr` (macOS arm64), con la licencia que informa pip-licenses. Las marcadas con † solo aparecen con el extra `ocr`.

| Raíz | Dependencia | Versión | Licencia |
|---|---|---|---|
| torch | filelock | 4.2.4 | MIT |
| torch | fsspec † | 2026.9.0 | BSD-3-Clause |
| torch | jinja2 † | 3.1.6 | BSD License |
| torch | markupsafe | 3.0.4 | BSD-3-Clause |
| torch | mpmath † | 1.3.0 | BSD License |
| torch | networkx | 3.7 | BSD-3-Clause |
| torch | setuptools † | 84.2.1 | MIT |
| torch | sympy † | 1.14.0 | BSD License |
| torch, transformers | typing-extensions | 4.16.0 | PSF-2.0 |
| transformers | annotated-doc | 0.0.5 | MIT |
| transformers | anyio | 4.15.1 | MIT |
| transformers | click | 8.5.0 | BSD-3-Clause |
| transformers | filelock, fsspec | ver arriba | ver arriba |
| transformers | h11 | 0.16.0 | MIT License |
| transformers | hf-xet † | 1.7.0 | Apache-2.0 |
| transformers | httpcore2 | 2.16.0 | BSD-3-Clause |
| transformers | httpx2 | 2.16.0 | BSD-3-Clause |
| transformers | huggingface-hub † | 2.3.0 | Apache Software License |
| transformers | idna | 3.20 | BSD-3-Clause |
| transformers | markdown-it-py | 4.2.0 | MIT License |
| transformers | mdurl | 0.1.2 | MIT License |
| transformers, opencv | numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| transformers | packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| transformers | pygments | 2.21.0 | BSD-2-Clause |
| transformers | pyyaml † | 6.0.3 | MIT License |
| transformers | regex † | 2026.9.29 | Apache-2.0 AND CNRI-Python (excepción) |
| transformers | rich | 15.0.0 | MIT License |
| transformers | safetensors † | 0.8.0 | Apache Software License |
| transformers | shellingham † | 1.5.4 | ISC License (ISCL) |
| transformers | tokenizers † | 0.23.3 | Apache Software License |
| transformers | tqdm † | 4.70.1 | MPL-2.0 AND MIT |
| transformers | truststore | 0.10.5 | MIT |
| transformers | typer † | 0.27.3 | MIT |
| opencv-python-headless (entorno por defecto) | numpy | 2.5.4 | ver arriba |

opencv-python-headless 5.0.0.93 declara "Apache 2.0" y solo depende de numpy, pero su rueda trae bibliotecas nativas que pip-licenses no ve (ver la sección siguiente).

### Bibliotecas nativas dentro de las ruedas (fuera del alcance de los verificadores)

Los verificadores leen la licencia declarada en los metadatos del paquete. No miran los binarios que una rueda trae dentro. Revisión manual:

| Rueda | Plataforma | Contenido relevante | Licencia verificada |
|---|---|---|---|
| opencv-python-headless 5.0.0.93 | Linux x86_64 (rueda descargada, hash del lock) y Linux arm64 (imagen `sketch2cad-api:local`) | FFmpeg (`libavcodec`, `libavformat`, `libavutil`, `libswscale`, `libswresample`), libvpx, libaom, OpenBLAS, OpenSSL 1.1, libpng, libgfortran (y libquadmath en x86_64) | `libavcodec`/`libavutil` informan "LGPL version 2.1 or later" y no se compilaron con `--enable-gpl` ni libx264. FFmpeg se enlaza dinámicamente y está bajo LGPL, como declara `LICENSE-3RD-PARTY.txt`. libgfortran es runtime de GCC (GPL-3.0-or-later con la GCC Runtime Library Exception 3.1, igual que libgomp). La licencia de libquadmath no se verificó |
| opencv-python-headless 5.0.0.93 | macOS arm64 (solo entornos de desarrollo) | 99 dylibs en `cv2/.dylibs`, entre ellas `libx264`, `libx265`, `librubberband`, `libvidstab` y `libpostproc`. `libavcodec` está compilada con `--enable-gpl --enable-version3 --enable-libx264 --enable-libx265` | `libavcodec` y `libavutil` informan "**GPL version 3 or later**", y `libavcodec` enlaza `libx264`/`libx265`. **GPL dentro de una dependencia del entorno por defecto** (ver decisiones abiertas) |
| torch 2.14.1+cpu | Linux x86_64 (rueda descargada, hash del lock) | `torch/lib/libgomp.so.1` (runtime OpenMP de GCC) | GCC runtime: GPL-3.0-or-later con la GCC Runtime Library Exception 3.1 (según `/usr/share/doc/libgcc-s1/copyright` de la imagen base Debian, que lista libgomp entre las bibliotecas con esa excepción) |
| torch 2.14.1 | macOS arm64 | `torch/lib/libomp.dylib` (OpenMP de LLVM) | Apache-2.0 WITH LLVM-exception (`third_party/llvm-openmp/LICENSE.txt`) |
| torch 2.14.1 | ambas | `licenses/third_party/cutlass/LICENSE.txt` es BSD-3-Clause, pero dice que `python/CuTeDSL` está bajo el EULA de NVIDIA | La rueda no trae CuTeDSL. Solo trae el generador de código de PyTorch `torch/_inductor/codegen/cutedsl/`, que es código propio de PyTorch. No se instala ningún paquete `nvidia-cutlass-dsl` |

### Resolución en Linux (CI y contenedores)

Solo pude ejecutar la auditoría completa sobre las ruedas de macOS arm64. Para Linux revisé los metadatos de `uv.lock` y dos ruedas descargadas.

- `uv.lock` fija torch en Linux al índice CPU (`[tool.uv.sources]`: `torch = [{ index = "pytorch-cpu", marker = "sys_platform == 'linux'" }]`). La versión bloqueada es `2.14.1+cpu` de `https://download.pytorch.org/whl/cpu`.
- **No hay paquetes `nvidia-*`, `triton` ni `cuda-*` en el lock.** La rueda Linux x86_64 de torch `2.14.1+cpu` solo declara como `Requires-Dist` filelock, typing-extensions, setuptools, sympy, networkx, jinja2 y fsspec (más extras opcionales no instalados). Su `License-Expression` es idéntica a la de la rueda de macOS.
- Paquetes que solo se resuelven en Linux: `torch 2.14.1+cpu` (en lugar de `torch 2.14.1`). Por marcador de plataforma, `colorama` y `tzdata` solo se instalan en Windows; `httpx2-jsfetch` solo en emscripten.
- El job `licenses-ocr` (ubuntu) es el que comprueba en CI la rueda `+cpu` real. Todavía no se ejecutó en GitHub Actions.

## Lo que esta auditoría NO cubre

- **Paquetes del sistema operativo de las imágenes Docker.** Las imágenes base son `python:3.12.13-slim` (Debian; API y worker), `node:24.22.1-alpine` (build web) y `nginx:1.30.6-alpine` (web). Contienen software GPL del sistema: la imagen de la API trae, por ejemplo, bash y coreutils, y el runtime de GCC; las imágenes Alpine traen busybox. No se escanearon. Tampoco se pasó un escáner de vulnerabilidades sobre las imágenes (Trivy, Grype).
- **El modelo TrOCR.** `microsoft/trocr-base-handwritten`, revisión `eaacaf452b06415df8f10bb6fad3a4c11e609406` (`Dockerfile.worker`, `TROCR_REVISION`). El directorio del modelo que se hornea en la imagen no incluye la ficha del modelo, porque `allow_patterns` solo copia `*.json`, `*.txt` y `model.safetensors`. Por eso la licencia se leyó de la ficha `README.md` de esa misma revisión en Hugging Face: `license: mit`. Procedencia de los pesos: según la ficha, el modelo está ajustado (fine-tuned) sobre el IAM Handwriting Database. El encoder se inicializó con pesos de BEiT y el decoder con pesos de RoBERTa. No se verificaron los términos de uso del dataset IAM ni las licencias de BEiT/RoBERTa. El código no fija ningún hash de `model.safetensors`; solo se fija la revisión.
- **Fuentes.** Los PDF usan las fuentes estándar base-14 de ReportLab (`Helvetica`, `Helvetica-Bold`) y no incrustan TTF propios. El DXF referencia por nombre la fuente `OpenSans-Regular.ttf` en su estilo de texto (`export/dxf_text.py`). No se revisó su licencia ni de dónde la obtiene el visor CAD. Tampoco se revisaron las fuentes del sistema en las imágenes ni las fuentes que usa el navegador en la web.
- **Binarios opcionales por plataforma del lado web** (por ejemplo los paquetes nativos de esbuild/rollup/lightningcss de otras plataformas). Solo se revisan los instalados en la plataforma local.
- **Acciones de GitHub y herramientas de CI** (actions/checkout, setup-uv, setup-node, pnpm/action-setup, la imagen de gitleaks). No forman parte del producto entregado.
- Ruedas de Windows y de macOS x86_64.

## Decisiones abiertas del dueño

1. **opencv-python-headless en macOS arm64 trae FFmpeg GPL-3.0-or-later y libx264/libx265.** Afecta solo a los entornos de desarrollo en macOS. Las ruedas de Linux que usan la CI y los contenedores traen FFmpeg LGPL-2.1-or-later. La política prohíbe GPL sin excepción posible, pero aquí no se distribuye el binario de macOS y el verificador no lo detecta (metadatos "Apache 2.0"). Opciones: aceptar que la regla aplica a lo que se distribuye (imágenes Linux); construir opencv sin FFmpeg para macOS; o desarrollar dentro de contenedores Linux.
2. **torch en Linux trae `libgomp.so.1`, y opencv en Linux trae `libgfortran`. Las dos son runtime de GCC: GPL-3.0-or-later con la GCC Runtime Library Exception 3.1.** La excepción permite usar el runtime desde programas no GPL. Están en el mismo caso que el `libgcc_s` de cualquier imagen Debian. La excepción de torch queda `pending-owner-decision` (pasa con WARNING) hasta que el dueño confirme que un runtime GPL con esta excepción queda fuera de la prohibición.
3. **FFmpeg LGPL-2.1-or-later dentro de opencv (Linux).** Según DEC-6, cada LGPL necesita una excepción documentada. El verificador no puede expresarla porque la licencia declarada del paquete es Apache 2.0. Queda documentada aquí. El dueño debe confirmar si basta con esto o si hay que añadir una lista de binarios incluidos.
4. **Términos de los datos de entrenamiento del modelo OCR** (IAM Handwriting Database) y licencias de los pesos base (BEiT, RoBERTa). No están verificados.
5. **Spec §7.2** declara para OCR "MIT / Apache", y en la tabla falta torch (licencia compuesta). Conviene actualizar la tabla con la tabla de excepciones de este documento.
