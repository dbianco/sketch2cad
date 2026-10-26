"""Normalization of the text read by OCR on a user crop (REQ-18; spec section 10).

The recognizer returns free text such as ``"3,50"``, ``"3.50 m"`` or ``"3,5Omts"``; this
module turns it into a distance in metres or explains why it cannot. It is pure: no I/O,
no configuration lookup and it never raises. The caller resolves the preselection
threshold (``get_param("ocr.min_confidence", ...)``) and passes it to :func:`preselect`.

Rules, in order:

1. Unicode is folded with NFKC (full-width digits, NBSP and other odd spaces), dash-like
   minus signs become ``-``, whitespace runs collapse to one space and trailing
   punctuation (``. , ; : ! ?``) is dropped.
2. A trailing unit is detected, case-insensitive, with or without a space: ``m``, ``mt``,
   ``mts``, ``mtr``, ``mtrs``, ``metro``, ``metros`` (metres), ``cm``, ``cms``
   (centimetres) and ``mm`` (millimetres). A bare number is in metres.
3. OCR confusions are fixed only inside the number and only when it already holds at
   least one real digit and nothing but digits, separators and confusable letters:
   ``O``/``o`` -> ``0``, ``l``/``I``/``|`` -> ``1``, ``S`` -> ``5``. ``"O,5"`` reads 0.5,
   but ``"O"``, ``"lO"`` or ``"abc"`` are not numbers.
4. Separators: one comma or point is the decimal mark (``"3,50"``, ``"3.5"``), except
   ``d{1,3}`` + separator + exactly three digits (``"1,234"``, ``"3.500"``), which could be
   a thousands group or a decimal and is refused as ``ambiguous``; a leading zero
   (``"0,350"``) removes the doubt. Repeated groups of the same separator are thousands
   (``"1.234.567"``) and a mix reads as thousands + decimal only when the grouping is
   well formed (``"1.234,50"`` and ``"1,234.50"`` -> 1234.5). Any other layout, a space
   inside the digits (``"3 50"``) or a leading separator (``",5"``) is ``ambiguous``.
5. The value must be finite and greater than zero (distances, as in V-04).

``"3 m 50"`` (metres and centimetres written apart) is not supported: ``non_numeric``.
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

Reason = Literal["empty", "non_numeric", "non_positive", "ambiguous"]
"""Why :attr:`NormalizedValue.value_m` is ``None``."""


@dataclass(frozen=True)
class NormalizedValue:
    """Outcome of :func:`normalize_ocr_text`.

    ``text`` is the canonical reading when a value was found (point as decimal mark, no
    thousands separator, confusions fixed, unit kept only for ``cm``/``mm``: ``"3.50"``,
    ``"350 cm"``) and the cleaned input otherwise. ``reason`` is ``None`` exactly when
    ``value_m`` is not ``None``.
    """

    value_m: float | None
    unit_detected: str | None
    text: str
    reason: Reason | None = None


_MINUS_SIGNS = dict.fromkeys(map(ord, "−‐‑‒–—―﹣"), "-")
_TRAILING_PUNCTUATION = ".,;:!?"
_CONFUSIONS = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5"})
_UNIT_DIVISORS = {"m": Decimal(1), "cm": Decimal(100), "mm": Decimal(1000)}
_UNIT_ALIASES = {
    "m": "m",
    "mt": "m",
    "mts": "m",
    "mtr": "m",
    "mtrs": "m",
    "metro": "m",
    "metros": "m",
    "cm": "cm",
    "cms": "cm",
    "mm": "mm",
}

_WHITESPACE = re.compile(r"\s+")
_UNIT = re.compile(r"\s?(metros?|mtrs?|mts?|cms?|mm|m)$", re.IGNORECASE)
_NUMBER_CHARS = re.compile(r"[+-]?[0-9OolI|S., ]*")
_SPACED_SEPARATOR = re.compile(r" ?([.,]) ?")
_INTEGER = re.compile(r"\d+")
_SINGLE_SEPARATOR = re.compile(r"(\d+)[.,](\d+)")
_THOUSANDS_LIKE = re.compile(r"[1-9]\d{0,2}[.,]\d{3}")
_GROUPED = re.compile(r"[1-9]\d{0,2}(?:([.,])\d{3})(?:\1\d{3})+")
_GROUPED_DECIMAL = re.compile(r"[1-9]\d{0,2}(?:\.\d{3})+,\d+|[1-9]\d{0,2}(?:,\d{3})+\.\d+")


def normalize_ocr_text(raw: str) -> NormalizedValue:
    """Read a distance in metres from OCR text; ``value_m`` is ``None`` with a ``reason``."""
    text = _clean(raw)
    if not text:
        return NormalizedValue(None, None, text, "empty")

    unit: str | None = None
    number = text
    unit_match = _UNIT.search(text)
    if unit_match is not None:
        unit = _UNIT_ALIASES[unit_match.group(1).lower()]
        number = text[: unit_match.start()].rstrip()

    if not _NUMBER_CHARS.fullmatch(number) or not any(c.isdigit() for c in number):
        return NormalizedValue(None, None, text, "non_numeric")

    sign = ""
    if number[0] in "+-":
        sign, number = number[0], number[1:].lstrip()
    if not number or number[0] in "+-":
        return NormalizedValue(None, None, text, "non_numeric")
    number = _SPACED_SEPARATOR.sub(r"\1", number.translate(_CONFUSIONS))
    if " " in number:
        return NormalizedValue(None, None, text, "ambiguous")

    canonical = _canonical_number(number)
    if canonical is None:
        return NormalizedValue(None, None, text, "ambiguous")

    try:
        value_m = float(Decimal(sign + canonical) / _UNIT_DIVISORS[unit or "m"])
    except (InvalidOperation, ArithmeticError, ValueError):
        return NormalizedValue(None, None, text, "non_numeric")
    if not math.isfinite(value_m):
        return NormalizedValue(None, None, text, "non_numeric")
    if value_m <= 0:
        return NormalizedValue(None, None, text, "non_positive")

    shown = canonical if unit in (None, "m") else f"{canonical} {unit}"
    return NormalizedValue(value_m, unit, shown)


def preselect(value: NormalizedValue, confidence: float, min_confidence: float) -> bool:
    """Whether the UI preselects the OCR value: a value and ``confidence >= min_confidence``.

    A confidence or threshold that is not a finite number in ``[0, 1]`` never preselects.
    """
    if value.value_m is None:
        return False
    if not (_is_probability(confidence) and _is_probability(min_confidence)):
        return False
    return confidence >= min_confidence


def _clean(raw: object) -> str:
    """NFKC, ASCII minus, single spaces, no surrounding spaces or trailing punctuation."""
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFKC", raw).translate(_MINUS_SIGNS)
    text = _WHITESPACE.sub(" ", text).strip()
    return text.rstrip(_TRAILING_PUNCTUATION).rstrip()


def _canonical_number(number: str) -> str | None:
    """Digits with an optional ``.`` decimal part, or ``None`` when the layout is ambiguous."""
    if _INTEGER.fullmatch(number):
        return number
    if _GROUPED.fullmatch(number):
        return number.replace(number[-4], "")
    if _GROUPED_DECIMAL.fullmatch(number):
        decimal_mark, thousands = (
            (",", ".") if number.rfind(",") > number.rfind(".") else (".", ",")
        )
        return number.replace(thousands, "").replace(decimal_mark, ".")
    single = _SINGLE_SEPARATOR.fullmatch(number)
    if single is None or _THOUSANDS_LIKE.fullmatch(number):
        return None
    return f"{single.group(1)}.{single.group(2)}"


def _is_probability(value: object) -> bool:
    return isinstance(value, int | float) and math.isfinite(value) and 0 <= value <= 1
