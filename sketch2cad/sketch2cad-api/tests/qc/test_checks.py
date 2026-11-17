"""Image quality checks on synthetic OpenCV sketches (H1-4, REQ-02)."""

import json
from typing import Any

import numpy as np
import pytest

from sketch2cad.config import Settings
from sketch2cad.qc import CheckName, QcResult, QcStatus, QcThresholds, evaluate, synthetic
from sketch2cad.qc.checks import contrast_range, laplacian_variance, to_gray

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://user:pass@localhost:5432/sketch2cad",
    "SESSION_SECRET": "test-session-secret",
    "SIGNING_KEY_PATH": "keys/signing-key.pem",
    "APP_ENV": "test",
}


def check(result: QcResult, name: CheckName) -> Any:
    return next(c for c in result.checks if c.name == name)


@pytest.fixture(scope="module")
def sharp_2000() -> Any:
    return synthetic.sketch(2000)


@pytest.mark.parametrize(
    ("side", "expected"),
    [(800, QcStatus.RED), (1200, QcStatus.AMBER), (3000, QcStatus.GREEN)],
)
def test_short_side_sets_the_semaphore(side: int, expected: QcStatus) -> None:
    result = evaluate(synthetic.sketch(side))

    short = check(result, CheckName.SHORT_SIDE)
    assert short.value == side
    assert short.status == expected
    assert result.status == expected


def test_sharp_3000_px_sketch_is_green_on_every_check() -> None:
    result = evaluate(synthetic.sketch(3000))

    assert result.status == QcStatus.GREEN
    assert [c.name for c in result.checks] == [
        CheckName.SHORT_SIDE,
        CheckName.LAPLACIAN_VARIANCE,
        CheckName.CONTRAST,
        CheckName.PAPER_CROP,
    ]
    assert all(c.status == QcStatus.GREEN for c in result.checks)


def test_laplacian_variance_30_is_red(sharp_2000: Any) -> None:
    image = synthetic.with_laplacian_variance(sharp_2000, 30.0)

    result = evaluate(image)

    sharpness = check(result, CheckName.LAPLACIAN_VARIANCE)
    assert sharpness.value == pytest.approx(30.0, abs=1.0)
    assert sharpness.status == QcStatus.RED
    assert sharpness.threshold == 40.0
    assert result.status == QcStatus.RED


def test_laplacian_variance_between_red_and_amber_is_amber(sharp_2000: Any) -> None:
    image = synthetic.with_laplacian_variance(sharp_2000, 70.0)

    sharpness = check(evaluate(image), CheckName.LAPLACIAN_VARIANCE)

    assert sharpness.status == QcStatus.AMBER
    assert sharpness.threshold == 100.0


def test_low_contrast_is_amber(sharp_2000: Any) -> None:
    faded = (sharp_2000.astype(np.float32) * 0.2 + 150).astype(np.uint8)

    contrast = check(evaluate(faded), CheckName.CONTRAST)

    assert contrast.value == pytest.approx(contrast_range(to_gray(faded)))
    assert contrast.value < 60
    assert contrast.status == QcStatus.AMBER


def test_paper_cut_by_the_frame_is_amber() -> None:
    result = evaluate(synthetic.sketch(2000, paper_inside=False))

    crop = check(result, CheckName.PAPER_CROP)
    assert crop.status == QcStatus.AMBER
    assert 0 < crop.value < 1
    assert result.status == QcStatus.AMBER


def test_paper_filling_the_frame_is_not_reported_as_cut(sharp_2000: Any) -> None:
    scan = sharp_2000[200:-200, 200:-200]

    assert check(evaluate(scan), CheckName.PAPER_CROP).status == QcStatus.GREEN


def test_thresholds_are_read_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("QC_MIN_SHORT_SIDE_RED", "1000")
    thresholds = QcThresholds.from_settings(Settings())  # pyright: ignore[reportCallIssue]

    image_950 = synthetic.sketch(950)

    side_800 = check(evaluate(synthetic.sketch(800), thresholds), CheckName.SHORT_SIDE)
    side_950_default = check(evaluate(image_950), CheckName.SHORT_SIDE)
    side_950 = check(evaluate(image_950, thresholds), CheckName.SHORT_SIDE)

    assert thresholds.min_short_side_red == 1000
    assert (side_800.status, side_800.threshold) == (QcStatus.RED, 1000)
    # 950 px is amber under the default red limit (900) and becomes red under 1000.
    assert (side_950_default.status, side_950_default.threshold) == (QcStatus.AMBER, 1500)
    assert (side_950.status, side_950.threshold) == (QcStatus.RED, 1000)


def test_default_thresholds_match_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)

    settings = Settings()  # pyright: ignore[reportCallIssue]

    assert QcThresholds.from_settings(settings) == QcThresholds()


@pytest.mark.parametrize(
    "bad_input",
    [
        None,
        "not an image",
        np.zeros((0, 0), dtype=np.uint8),
        np.zeros((10,), dtype=np.uint8),
        np.zeros((4, 4, 2), dtype=np.uint8),
        np.array([["a", "b"]]),
    ],
    ids=["none", "string", "empty", "one-dimensional", "two-channel", "strings-array"],
)
def test_bad_input_never_raises_and_is_red(bad_input: object) -> None:
    result = evaluate(bad_input)

    assert result.status == QcStatus.RED
    assert [c.name for c in result.checks] == [CheckName.INPUT]
    assert result.checks[0].message


@pytest.mark.parametrize(
    "image",
    [
        np.zeros((1, 1), dtype=np.uint8),
        np.full((50, 50, 4), 255, dtype=np.uint8),
        np.full((50, 50, 1), 128, dtype=np.uint8),
        np.full((50, 50), 40000, dtype=np.uint16),
        np.random.default_rng(1).random((50, 50)),
    ],
    ids=["single-pixel", "bgra", "single-channel", "uint16", "float"],
)
def test_degenerate_images_still_return_every_check(image: Any) -> None:
    result = evaluate(image)

    assert len(result.checks) == 4
    assert result.status == QcStatus.RED  # every one of them is below 900 px


def test_result_dict_is_the_json_stored_in_source_qc(sharp_2000: Any) -> None:
    stored = evaluate(sharp_2000).to_dict()

    assert json.loads(json.dumps(stored)) == stored
    assert stored["status"] == "green"
    assert set(stored["checks"][0]) == {
        "name",
        "status",
        "value",
        "threshold",
        "message",
        "cause",
        "action",
    }
    assert stored["checks"][3]["threshold"] is None


def test_laplacian_variance_of_flat_image_is_zero() -> None:
    assert laplacian_variance(np.full((20, 20), 200, dtype=np.uint8)) == 0.0
