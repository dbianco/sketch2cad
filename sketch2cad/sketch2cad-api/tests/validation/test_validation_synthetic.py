"""Synthetic validation set generator (task 001-115, REQ-20, H6-5); no database.

Loads ``validation/synthetic/generate.py`` (outside the package) and checks the count, the
ground-truth schema, byte determinism and that every image runs through the real vision
pipeline and quality checks. Recovery is only sanity-checked: CA-04 has its own benchmark.
"""

import importlib.util
import json
import math
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from sketch2cad.calibration.rectify import rectify_image
from sketch2cad.geometry.homography import apply_h, homography_from_json
from sketch2cad.geometry.metrics import baseline_offset_point, triangulate
from sketch2cad.qc.checks import QcStatus, evaluate
from sketch2cad.vision import suggest_polylines

SCRIPT = Path(__file__).resolve().parents[3] / "validation" / "synthetic" / "generate.py"
COUNT = 4
"""One image of each kind keeps the test fast (about 1 s per generation)."""


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_generate = _load(SCRIPT, "validation_synthetic_generate")
main = cast(Callable[[list[str]], int], _generate.main)
KINDS = cast(tuple[str, ...], _generate.KINDS)


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("synthetic")
    assert main(["--count", str(COUNT), "--out", str(out), "--seed", "7"]) == 0
    return out


def _truths(out: Path) -> list[dict[str, Any]]:
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    return [
        json.loads((out / item["truth"]).read_text(encoding="utf-8")) for item in index["items"]
    ]


def test_count_creates_one_image_and_one_truth_per_index(generated: Path) -> None:
    images = sorted(p.name for p in generated.glob("*.jpg"))
    truths = sorted(p.name for p in generated.glob("*.json") if p.name != "index.json")

    assert len(images) == len(truths) == COUNT
    assert [t["kind"] for t in _truths(generated)] == list(KINDS)


def test_ground_truth_has_the_documented_schema(generated: Path) -> None:
    for truth in _truths(generated):
        image = cv2.imread(str(generated / truth["image"]))
        assert image is not None and [image.shape[1], image.shape[0]] == truth["size_px"]
        assert truth["schema"] == "sketch2cad-validation-synthetic/1" and truth["px_per_mm"] == 10
        for segment in truth["segments"]:
            assert len(segment["paper_mm"]) == len(segment["image_px"]) == 2
        if truth["kind"] == "oblique_pattern":
            assert truth["rectified"] is False and set(truth["homography"]) == {
                "paper_mm_to_image_px",
                "image_px_to_paper_mm",
            }
            assert truth["pattern"]["rect_width_mm"] > 0 and truth["pattern"]["scale_bar_mm"] > 0
            continue
        point_ids = {p["id"] for p in truth["points"]}
        assert truth["rectified"] is True and {"A", "B"} <= point_ids
        for dimension in truth["dimensions"]:
            assert dimension["value_m"] > 0 and set(dimension["points"]) <= point_ids
            assert dimension["text"] == f"{dimension['value_m']:.2f}".replace(".", ",")


def test_same_seed_gives_the_same_bytes(generated: Path, tmp_path: Path) -> None:
    assert main(["--count", str(COUNT), "--out", str(tmp_path), "--seed", "7"]) == 0

    for first in sorted(generated.iterdir()):
        assert (tmp_path / first.name).read_bytes() == first.read_bytes(), first.name


def test_expected_evidence_coordinates_agree_with_the_product_geometry(generated: Path) -> None:
    for truth in _truths(generated):
        world = {p["id"]: p["world_m"] for p in truth["points"]}
        for item in truth["evidence"]:
            a, b = world[item["refs"][0]], world[item["refs"][1]]
            if item["method"] == "triangulation":
                hint = world[item["id"]]
                point = triangulate(a, b, item["d_a_m"], item["d_b_m"], "nearest", hint).point
            else:
                point = baseline_offset_point(a, b, item["s_m"], item["d_m"], item["side"])
            assert point is not None
            assert math.dist(point, item["expected_world_m"]) < 1e-6
            # Written values are rounded to the centimetre: the truth stays within 1 cm.
            assert math.dist(point, world[item["id"]]) < 0.01


def _read(path: Path) -> npt.NDArray[np.uint8]:
    image = cv2.imread(str(path))
    assert image is not None, path.name
    return np.asarray(image, dtype=np.uint8)


def test_images_run_through_quality_checks_and_suggestions(generated: Path) -> None:
    for truth in _truths(generated):
        image = _read(generated / truth["image"])
        assert evaluate(image).status in set(QcStatus)
        if truth["kind"] == "oblique_pattern":
            pattern = truth["pattern"]
            to_paper = homography_from_json(truth["homography"]["image_px_to_paper_mm"])
            image_px = [(float(x), float(y)) for x, y in pattern["rect_corners_image_px"]]
            corners = apply_h(to_paper, image_px)
            assert np.allclose(corners, pattern["rect_corners_paper_mm"], atol=1e-3)
            image = np.asarray(rectify_image(image, to_paper, tuple(pattern["sheet_mm"])))
            _assert_dots_within_1_mm(image, pattern["dots_paper_mm"])
        assert suggest_polylines(image), truth["kind"]


def _assert_dots_within_1_mm(rectified: npt.NDArray[np.uint8], dots_mm: list[Any]) -> None:
    """CA-02 style: the printed dots land where the pattern says after rectification."""
    gray = np.asarray(cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
    _, ink = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    areas = np.asarray(stats, dtype=np.int64)[:, cv2.CC_STAT_AREA]
    found = np.asarray(centroids, dtype=np.float64) / 10.0
    blobs = [
        (float(found[i, 0]), float(found[i, 1]))
        for i in range(1, count)
        if 300 <= int(areas[i]) <= 1500
    ]
    for dot in dots_mm:
        assert min(math.dist(dot, blob) for blob in blobs) < 1.0
