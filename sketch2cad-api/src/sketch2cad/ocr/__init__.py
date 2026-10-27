"""Assisted OCR of handwritten dimensions on a user crop (REQ-18; spec section 10).

:func:`normalize_ocr_text` turns the recognizer text into metres and :func:`preselect`
applies the ``ocr.min_confidence`` threshold resolved by the caller.
"""

from sketch2cad.ocr.normalize import NormalizedValue, Reason, normalize_ocr_text, preselect

__all__ = ["NormalizedValue", "Reason", "normalize_ocr_text", "preselect"]
