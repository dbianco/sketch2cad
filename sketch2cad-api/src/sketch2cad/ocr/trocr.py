"""TrOCR reading of a crop marked by the user (REQ-18, US-13; spec sections 10 and 17).

The model is ``microsoft/trocr-base-handwritten`` at the pinned :data:`TROCR_REVISION`,
run on CPU. It is never downloaded at run time: the worker image (``Dockerfile.worker``)
bakes the files into :data:`DEFAULT_MODEL_DIR` at build time and runs with
``HF_HUB_OFFLINE=1``. Elsewhere (development, the ``slow`` tests) ``TROCR_MODEL_DIR``
points at a local copy; when it is unset and the default directory does not exist, the
model is fetched once from the Hugging Face cache or hub at the pinned revision.

``torch`` and ``transformers`` come from the optional ``ocr`` extra and are imported only
inside :func:`load_model`, so importing this module (the API does, to validate boxes)
needs neither. The model loads once per process (thread-safe singleton) and runs under
``torch.inference_mode``. torch picks its default intra-op thread count (physical cores);
``OMP_NUM_THREADS`` caps it, as for any torch process. The worker runs one job at a time.

Pipeline of :func:`ocr_crop`: validate the box against the image (:func:`validate_box`;
:class:`CropError` is a 422 for the API), crop in ORIGINAL image pixels, tighten the crop
to the ink with a margin of :data:`INK_MARGIN` x the ink height (users draw loose boxes;
on synthetic renders this raised exact readings from 66 % to 75 %), downscale to at most
:data:`MAX_INPUT_SIDE_PX`, recognize with greedy decoding, then
:func:`sketch2cad.ocr.normalize.normalize_ocr_text`.

``confidence`` is the arithmetic mean of the probabilities of the generated tokens (end
token included), from ``compute_transition_scores(..., normalize_logits=True)``: 1.0 when
the model was sure of every token. It is not calibrated against real sketches yet (H6).
"""

import importlib
import logging
import math
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from sketch2cad.ocr.normalize import NormalizedValue, normalize_ocr_text, preselect

logger = logging.getLogger(__name__)

OCR_JOB_KIND = "ocr_crop"
"""``job_meta.kind`` of OCR jobs (:mod:`sketch2cad.worker.tasks_ocr`)."""
TROCR_MODEL_ID = "microsoft/trocr-base-handwritten"
TROCR_REVISION = "eaacaf452b06415df8f10bb6fad3a4c11e609406"
"""Commit of the model repository; ``Dockerfile.worker`` downloads the same one."""
OCR_MODEL_VERSION = f"trocr-base-handwritten@{TROCR_REVISION}"
"""``job_meta.model_version`` of OCR jobs and ``software.ocr_model`` of the manifest."""
MODEL_DIR_ENV = "TROCR_MODEL_DIR"
DEFAULT_MODEL_DIR = Path("/opt/models/trocr-base-handwritten")

MIN_BOX_SIDE_PX = 8
"""Smaller boxes cannot hold a legible number (provisional, agent's choice)."""
MAX_BOX_SIDE_PX = 2000
"""A dimension label, not a page: full-page OCR is out of the MVP (provisional)."""
MAX_INPUT_SIDE_PX = 1000
"""Crops are downscaled to this longest side; the processor resizes to 384 x 384 anyway."""
INK_MARGIN = 0.25
MIN_INK_CONTRAST = 32
"""Grey-level range below which the crop is left as is (no ink to tighten on)."""
MAX_NEW_TOKENS = 16


class CropError(ValueError):
    """The box is not a valid crop of the image (API 422)."""


class ModelUnavailableError(RuntimeError):
    """The OCR dependencies or the model files are missing, or the model does not load."""


@dataclass(frozen=True)
class OcrResult:
    """Outcome of :func:`ocr_crop`: the spec ``{text, value_m, confidence}`` plus details.

    ``raw_text`` is what the model read; ``text`` is the normalized reading.
    """

    text: str
    raw_text: str
    value_m: float | None
    unit_detected: str | None
    reason: str | None
    confidence: float

    def to_dict(self, min_confidence: float) -> dict[str, Any]:
        """JSON stored as the job result; ``preselect`` applies ``ocr.min_confidence``."""
        value = NormalizedValue(self.value_m, self.unit_detected, self.text)
        return {
            "text": self.text,
            "raw_text": self.raw_text,
            "value_m": self.value_m,
            "unit_detected": self.unit_detected,
            "reason": self.reason,
            "confidence": self.confidence,
            "preselect": preselect(value, self.confidence, min_confidence),
            "min_confidence": min_confidence,
            "model_version": OCR_MODEL_VERSION,
        }


def validate_box(box_px: Sequence[Any], width: int, height: int) -> tuple[int, int, int, int]:
    """``[x0, y0, x1, y1]`` integer pixels with ``0 <= x0 < x1 <= width`` (same for y).

    Raises :class:`CropError` for another shape, a box outside the image, or a side below
    :data:`MIN_BOX_SIDE_PX` or above :data:`MAX_BOX_SIDE_PX`.
    """
    if len(box_px) != 4 or not all(type(v) is int for v in box_px):
        raise CropError("box_px must be four integers [x0, y0, x1, y1].")
    x0, y0, x1, y1 = (int(v) for v in box_px)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise CropError(f"box_px must lie inside the image ({width} x {height} px).")
    if min(x1 - x0, y1 - y0) < MIN_BOX_SIDE_PX:
        raise CropError(f"box_px sides must be at least {MIN_BOX_SIDE_PX} px.")
    if max(x1 - x0, y1 - y0) > MAX_BOX_SIDE_PX:
        raise CropError(f"box_px sides must be at most {MAX_BOX_SIDE_PX} px.")
    return x0, y0, x1, y1


def prepare_crop(crop: Image.Image) -> Image.Image:
    """RGB crop tightened to its ink (with margin) and downscaled for the recognizer."""
    rgb = crop.convert("RGB")
    gray = np.asarray(rgb.convert("L"), dtype=np.int16)
    low, high = int(gray.min()), int(gray.max())
    if high - low >= MIN_INK_CONTRAST:
        rows, cols = np.nonzero(gray < (low + high) / 2)
        top, bottom = int(rows.min()), int(rows.max()) + 1
        margin = math.ceil(INK_MARGIN * (bottom - top))
        rgb = rgb.crop(
            (
                max(0, int(cols.min()) - margin),
                max(0, top - margin),
                min(rgb.width, int(cols.max()) + 1 + margin),
                min(rgb.height, bottom + margin),
            )
        )
    if max(rgb.size) > MAX_INPUT_SIDE_PX:
        rgb.thumbnail((MAX_INPUT_SIDE_PX, MAX_INPUT_SIDE_PX), Image.Resampling.LANCZOS)
    return rgb


@dataclass(frozen=True)
class _Model:
    torch: Any
    processor: Any
    model: Any


_lock = threading.Lock()
_loaded: _Model | None = None


def model_source() -> str:
    """Directory of the model files, or the hub id when no local copy is configured."""
    configured = os.environ.get(MODEL_DIR_ENV)
    if configured:
        return configured
    return str(DEFAULT_MODEL_DIR) if DEFAULT_MODEL_DIR.is_dir() else TROCR_MODEL_ID


def load_model() -> _Model:
    """Load processor and model once per process; raise :class:`ModelUnavailableError`."""
    global _loaded
    with _lock:
        if _loaded is not None:
            return _loaded
        source = model_source()
        local = source != TROCR_MODEL_ID
        try:
            # Untyped optional dependencies: imported by name so pyright and the default
            # environment (without the extra) never need them.
            torch: Any = importlib.import_module("torch")
            transformers: Any = importlib.import_module("transformers")
        except ImportError as exc:
            raise ModelUnavailableError("the `ocr` extra is not installed") from exc
        if local and not Path(source).is_dir():
            raise ModelUnavailableError(f"model directory {source} does not exist")
        revision = None if local else TROCR_REVISION
        try:
            processor = transformers.TrOCRProcessor.from_pretrained(source, revision=revision)
            model = transformers.VisionEncoderDecoderModel.from_pretrained(
                source, revision=revision
            )
        except (OSError, ValueError) as exc:
            raise ModelUnavailableError(f"model does not load: {type(exc).__name__}") from exc
        model.eval()
        _loaded = _Model(torch, processor, model)
        logger.info("Loaded OCR model %s", OCR_MODEL_VERSION)
        return _loaded


def recognize(image: Image.Image) -> tuple[str, float]:
    """Text read in ``image`` (one line) and its mean token probability in ``[0, 1]``."""
    loaded = load_model()
    with loaded.torch.inference_mode():
        pixel_values = loaded.processor(images=image.convert("RGB"), return_tensors="pt")
        output = loaded.model.generate(
            pixel_values.pixel_values,
            max_new_tokens=MAX_NEW_TOKENS,
            num_beams=1,
            do_sample=False,
            output_scores=True,
            return_dict_in_generate=True,
        )
        scores = loaded.model.compute_transition_scores(
            output.sequences, output.scores, normalize_logits=True
        )
        probabilities = [float(p) for p in scores.exp()[0].tolist()]
    text = str(loaded.processor.batch_decode(output.sequences, skip_special_tokens=True)[0])
    confidence = sum(probabilities) / len(probabilities) if probabilities else 0.0
    return text.strip(), min(1.0, max(0.0, confidence))


def ocr_crop(image: Image.Image, box_px: Sequence[Any]) -> OcrResult:
    """Read the dimension inside ``box_px`` (original image pixels) of ``image``.

    Raises :class:`CropError` for an invalid box and :class:`ModelUnavailableError` when
    the model cannot be used.
    """
    box = validate_box(box_px, image.width, image.height)
    raw, confidence = recognize(prepare_crop(image.crop(box)))
    value = normalize_ocr_text(raw)
    return OcrResult(
        text=value.text,
        raw_text=raw,
        value_m=value.value_m,
        unit_detected=value.unit_detected,
        reason=value.reason,
        confidence=round(confidence, 4),
    )
