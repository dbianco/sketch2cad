"""OCR output normalization and preselection threshold (REQ-18, H5-5)."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sketch2cad.config_params import code_default
from sketch2cad.ocr import NormalizedValue, normalize_ocr_text, preselect

# (raw OCR text, value_m, unit_detected, reason)
CASES: list[tuple[str, float | None, str | None, str | None]] = [
    ("3,50", 3.5, None, None),
    ("3.50 m", 3.5, "m", None),
    ("3,50mts", 3.5, "m", None),
    ("3.5m", 3.5, "m", None),
    (" 3,50 M ", 3.5, "m", None),
    ("3,50 metros", 3.5, "m", None),
    ("3,50 m.", 3.5, "m", None),
    ("3, 50", 3.5, None, None),
    ("+3", 3.0, None, None),
    ("12", 12.0, None, None),
    ("350 cm", 3.5, "cm", None),
    ("350CMS", 3.5, "cm", None),
    ("3500 mm", 3.5, "mm", None),
    ("1.234,50", 1234.5, None, None),
    ("1,234.50", 1234.5, None, None),
    ("1.234.567", 1234567.0, None, None),
    ("0,350", 0.35, None, None),
    ("3,50 m", 3.5, "m", None),
    (" ３,５０ m", 3.5, "m", None),
    ("O,5", 0.5, None, None),
    ("3,5Om", 3.5, "m", None),
    ("l,2O", 1.2, None, None),
    ("3S cm", 0.35, "cm", None),
    ("abc", None, None, "non_numeric"),
    ("", None, None, "empty"),
    ("    ", None, None, "empty"),
    ("--", None, None, "non_numeric"),
    ("m", None, None, "non_numeric"),
    ("O", None, None, "non_numeric"),
    ("lO", None, None, "non_numeric"),
    ("3 m 50", None, None, "non_numeric"),
    ("1e5", None, None, "non_numeric"),
    ("nan", None, None, "non_numeric"),
    ("inf", None, None, "non_numeric"),
    ("9" * 400, None, None, "non_numeric"),
    ("0", None, None, "non_positive"),
    ("0,00 m", None, None, "non_positive"),
    ("-2", None, None, "non_positive"),
    ("−2,5", None, None, "non_positive"),
    ("1,234", None, None, "ambiguous"),
    ("3.500 m", None, None, "ambiguous"),
    ("3 50", None, None, "ambiguous"),
    (",5", None, None, "ambiguous"),
    ("1,2.3", None, None, "ambiguous"),
    ("1.23.456", None, None, "ambiguous"),
]


@pytest.mark.parametrize(("raw", "value_m", "unit", "reason"), CASES)
def test_normalize_table(
    raw: str, value_m: float | None, unit: str | None, reason: str | None
) -> None:
    result = normalize_ocr_text(raw)
    assert result.reason == reason
    assert result.unit_detected == unit
    if value_m is None:
        assert result.value_m is None
    else:
        assert result.value_m == pytest.approx(value_m, rel=1e-12)  # pyright: ignore[reportUnknownMemberType]


def test_text_is_the_canonical_reading() -> None:
    assert normalize_ocr_text("3,50 mts").text == "3.50"
    assert normalize_ocr_text("1.234,50").text == "1234.50"
    assert normalize_ocr_text("35O cm").text == "350 cm"
    assert normalize_ocr_text("  abc  ").text == "abc"


def test_non_string_input_is_empty() -> None:
    assert normalize_ocr_text(None).reason == "empty"  # pyright: ignore[reportArgumentType]


_SUFFIXES = ["", "m", " m", " M", "mts", " metros", "."]


@given(
    cents=st.integers(min_value=1, max_value=100_000),
    decimal_mark=st.sampled_from([",", "."]),
    suffix=st.sampled_from(_SUFFIXES),
)
def test_round_trip_metres(cents: int, decimal_mark: str, suffix: str) -> None:
    text = f"{cents // 100}{decimal_mark}{cents % 100:02d}{suffix}"
    assert normalize_ocr_text(text).value_m == pytest.approx(cents / 100, rel=1e-12)  # pyright: ignore[reportUnknownMemberType]


@given(
    x=st.floats(min_value=0.01, max_value=1000, exclude_min=True, exclude_max=True),
    decimal_mark=st.sampled_from([",", "."]),
    suffix=st.sampled_from(_SUFFIXES),
)
def test_round_trip_formatted_floats(x: float, decimal_mark: str, suffix: str) -> None:
    formatted = f"{x:.2f}"
    result = normalize_ocr_text(formatted.replace(".", decimal_mark) + suffix)
    assert result.value_m == pytest.approx(float(formatted), rel=1e-12)  # pyright: ignore[reportUnknownMemberType]


@given(
    amount=st.integers(min_value=1, max_value=999_999),
    unit=st.sampled_from([("cm", 100), (" cm", 100), ("mm", 1000), (" MM", 1000)]),
)
def test_round_trip_sub_metre_units(amount: int, unit: tuple[str, int]) -> None:
    suffix, divisor = unit
    result = normalize_ocr_text(f"{amount}{suffix}")
    assert result.value_m == pytest.approx(amount / divisor, rel=1e-12)  # pyright: ignore[reportUnknownMemberType]


@given(st.text())
def test_never_raises_on_arbitrary_text(raw: str) -> None:
    result = normalize_ocr_text(raw)
    assert (result.value_m is None) == (result.reason is not None)
    if result.value_m is not None:
        assert math.isfinite(result.value_m)
        assert result.value_m > 0


@given(st.text(alphabet="0123456789OolI|S.,+-  −mtcsMT"))
def test_never_raises_on_number_like_text(raw: str) -> None:
    result = normalize_ocr_text(raw)
    assert (result.value_m is None) == (result.reason is not None)


VALUE = NormalizedValue(3.5, None, "3.50")
NO_VALUE = NormalizedValue(None, None, "abc", "non_numeric")


@pytest.mark.parametrize(
    ("value", "confidence", "expected"),
    [
        (VALUE, 0.6, True),
        (VALUE, 0.82, True),
        (VALUE, 1.0, True),
        (VALUE, 0.5999, False),
        (VALUE, 0.0, False),
        (VALUE, -0.1, False),
        (VALUE, 1.01, False),
        (VALUE, math.nan, False),
        (VALUE, math.inf, False),
        (NO_VALUE, 0.99, False),
    ],
)
def test_preselect_boundaries(value: NormalizedValue, confidence: float, expected: bool) -> None:
    assert preselect(value, confidence, 0.6) is expected


@pytest.mark.parametrize("threshold", [math.nan, -0.1, 1.5])
def test_preselect_rejects_invalid_threshold(threshold: float) -> None:
    assert preselect(VALUE, 0.9, threshold) is False


def test_preselect_default_threshold_is_the_registered_parameter() -> None:
    threshold = code_default("ocr.min_confidence")
    assert threshold == 0.6
    assert isinstance(threshold, float)
    assert preselect(normalize_ocr_text("3,50"), 0.6, threshold)
