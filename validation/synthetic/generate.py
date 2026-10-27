"""Synthetic validation set with known geometry (task 001-115, REQ-20, jotdown H6-5).

    python validation/synthetic/generate.py --count 20 [--out DIR] [--seed N]
        [--grid mixed|on|off] [--font plain|simplex|script]

Needs the API environment (it reuses ``sketch2cad``); from the repository root either
``uv run --project sketch2cad-api python validation/synthetic/generate.py --count 20`` or,
with ``sketch2cad-api/.venv`` activated, the plain ``python`` command above.

Image ``i`` (1-based) is of kind ``KINDS[(i - 1) % 4]``: the three field practices drawn
on a rectified A4 sheet (10 px per paper mm, like the pipeline input) and an oblique photo
of a printed pattern. Each ``NN-<kind>.jpg`` has a ``NN-<kind>.json`` with its ground
truth (schema in ``validation/README.md``); ``index.json`` lists them.

Deterministic: image ``i`` draws only from ``numpy.random.default_rng([seed, i])``, so the
same seed gives the same bytes on two runs on the same machine and environment (same
NumPy/OpenCV builds); the first images do not change with ``--count``. Other platforms or
library versions may differ in a few pixels.
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

try:
    import cv2
    import numpy as np

    from sketch2cad.calibration.rectify import PX_PER_MM
    from sketch2cad.geometry import homography_from_4_points
    from sketch2cad.geometry.homography import apply_h, homography_to_json, invert_h
    from sketch2cad.qc.synthetic import DESK_BGR
    from sketch2cad.vision.synthetic_sketch import (  # pyright: ignore[reportPrivateUsage]
        TrueSegment,
        _add_specks,
        _draw_stroke,
        _paper,
    )
except ImportError:  # pragma: no cover - depends on how the script is launched
    sys.exit(
        "sketch2cad, NumPy or OpenCV is missing: run from the repository root with\n"
        "  uv run --project sketch2cad-api python validation/synthetic/generate.py ..."
    )

SCHEMA = "sketch2cad-validation-synthetic/1"
KINDS = ("schematic", "triangulation", "baseline_offset", "oblique_pattern")
SHEET_MM = (297.0, 210.0)
MARGIN_MM = 30.0
GRID_PITCH_MM = 5.0
GRID_BGR_FACTOR = (0.72, 0.88, 1.0)
"""Light orange grid: multiplies the paper's blue, green and red channels."""
FONTS = {
    "plain": cv2.FONT_HERSHEY_PLAIN,
    "simplex": cv2.FONT_HERSHEY_SIMPLEX,
    "script": cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
}
FONT_HEIGHT_PX = 34
"""Cap height of the written numbers, about 3.4 mm of paper."""
VERTEX_JITTER_MM = 0.6
PHOTO_SIZE = (3200, 2400)
PATTERN_RECT_MM = (250.0, 160.0)
PATTERN_DOT_PITCH_MM = 50.0
SCALE_BAR_MM = 100.0
JPEG_QUALITY = 92
"""Photo-like output; PNG of the noisy paper would be about 5 MB per image."""

Point = tuple[float, float]


def written(value_m: float) -> str:
    """The value as a technician writes it: two decimals, comma separator."""
    return f"{value_m:.2f}".replace(".", ",")


def _r(values: Any, digits: int = 4) -> Any:
    if isinstance(values, (list, tuple)):
        return [_r(v, digits) for v in values]  # pyright: ignore[reportUnknownVariableType]
    return round(float(values), digits)


# --- Scenes in world metres (Y up) ---------------------------------------------------------


def _scene(kind: str, rng: np.random.Generator) -> dict[str, Any]:
    """Points, segments, dimensions and evidence of one field practice, in world metres."""
    step = lambda lo, hi: round(float(rng.uniform(lo, hi)) / 0.05) * 0.05  # noqa: E731
    if kind == "baseline_offset":
        length, width = step(20, 30), step(6, 8)
        pts: dict[str, Point] = {
            "E0": (-2.0, 0.0),
            "E1": (length - 2, 0.0),
            "T0": (-2.0, width),
            "T1": (length - 2, width),
            "A": (0.0, 0.0),
            "B": (10.0, 0.0),
            "W0": (-1.0, 0.0),
            "W1": (-1.0, width),
        }
        segs = [("baseline", "E0", "E1"), ("road_edge", "T0", "T1"), ("measure", "W0", "W1")]
        dims = [("A", "B", None, 0.5, 60), ("W0", "W1", "s3", 0.5, 60)]
        evidence: list[dict[str, Any]] = []
        along = [1.0 + 3.5 * k + float(rng.uniform(0.0, 2.0)) for k in range(3)]
        for k, s in enumerate(along, start=1):
            s_m, d_m = round(s * (length - 4) / 12, 2), round(float(rng.uniform(1.5, width - 1)), 2)
            pts[f"F{k}"], pts[f"P{k}"] = (s_m, 0.0), (s_m, d_m)
            segs.append(("offset", f"F{k}", f"P{k}"))
            dims += [("A", f"F{k}", None, 1.0, 140), (f"F{k}", f"P{k}", f"s{len(segs)}", 0.5, 60)]
            evidence.append(
                {
                    "id": f"P{k}",
                    "label": str(k),
                    "method": "baseline_offset",
                    "refs": ["A", "B"],
                    "s_m": s_m,
                    "d_m": d_m,
                    "side": "left",
                    "expected_world_m": [s_m, d_m],
                }
            )
        return {"points": pts, "segments": segs, "dimensions": dims, "evidence": evidence}
    width, height = step(4, 7), step(3, 5)
    pts = {"A": (0.0, 0.0), "B": (width, 0.0), "C": (width, height), "D": (0.0, height)}
    segs = [("wall", "A", "B"), ("wall", "B", "C"), ("wall", "C", "D"), ("wall", "D", "A")]
    dims = [("A", "B", "s1", 0.5, 60), ("D", "A", "s4", 0.5, 60)]
    evidence = []
    if kind == "schematic":
        x, h = step(0.35 * width, 0.65 * width), step(0.5 * height, 0.8 * height)
        pts["G"], pts["H"] = (x, 0.0), (x, h)
        segs.append(("wall", "G", "H"))
        dims += [("G", "H", "s5", 0.5, 60), ("A", "G", None, 0.5, 140)]
    else:
        for k, (lo, hi) in enumerate(((0.15, 0.4), (0.6, 0.85)), start=1):
            p = (float(rng.uniform(lo, hi)) * width, float(rng.uniform(0.25, 0.85)) * height)
            d_a, d_b = round(math.dist(p, pts["A"]), 2), round(math.dist(p, pts["B"]), 2)
            pts[f"P{k}"] = p
            segs += [("measure", "A", f"P{k}"), ("measure", "B", f"P{k}")]
            t = 0.35 if k == 1 else 0.65
            dims += [
                ("A", f"P{k}", f"s{len(segs) - 1}", t, 50),
                ("B", f"P{k}", f"s{len(segs)}", t, 50),
            ]
            evidence.append(
                {
                    "id": f"P{k}",
                    "label": str(k),
                    "method": "triangulation",
                    "refs": ["A", "B"],
                    "d_a_m": d_a,
                    "d_b_m": d_b,
                    "side": "left",
                    "expected_world_m": list(_circle_left(pts["A"], pts["B"], d_a, d_b)),
                }
            )
    return {"points": pts, "segments": segs, "dimensions": dims, "evidence": evidence}


def _circle_left(a: Point, b: Point, d_a: float, d_b: float) -> Point:
    """Intersection of circles (A, d_a) and (B, d_b) to the left of A->B (Y-up world)."""
    base = math.dist(a, b)
    ux, uy = (b[0] - a[0]) / base, (b[1] - a[1]) / base
    along = (d_a**2 - d_b**2 + base**2) / (2 * base)
    across = math.sqrt(max(d_a**2 - along**2, 0.0))
    return (a[0] + along * ux - across * uy, a[1] + along * uy + across * ux)


# --- Drawing ---------------------------------------------------------------------------------


def _ink_text(
    ink: np.ndarray,
    text: str,
    centre: Point,
    angle_deg: float,
    font: int,
    shade: int,
    rng: np.random.Generator,
) -> list[int]:
    """Write ``text`` centred at ``centre`` (px), rotated and slightly sheared; its box."""
    scale = cv2.getFontScaleFromHeight(font, FONT_HEIGHT_PX, 3)
    (w, h), base = cv2.getTextSize(text, font, scale, 3)
    side = int(math.hypot(w, h + base)) + 16
    patch = np.zeros((side, side), np.uint8)
    org = ((side - w) // 2, (side + h) // 2)
    cv2.putText(patch, text, org, font, scale, 255, int(rng.integers(3, 5)), cv2.LINE_AA)
    matrix = cv2.getRotationMatrix2D((side / 2, side / 2), angle_deg, 1.0)
    matrix[0, 1] += float(rng.uniform(-0.15, 0.15))
    patch = cv2.warpAffine(patch, matrix, (side, side), flags=cv2.INTER_LINEAR)
    left, top = round(centre[0] - side / 2), round(centre[1] - side / 2)
    window = ink[top : top + side, left : left + side]
    alpha = patch[: window.shape[0], : window.shape[1]].astype(np.float32) / 255.0
    window *= 1.0 - alpha * (1.0 - shade / 240.0)
    rows, cols = np.nonzero(patch)
    return [
        left + int(cols.min()),
        top + int(rows.min()),
        left + int(cols.max()) + 1,
        top + int(rows.max()) + 1,
    ]


def _to_paper(
    pts: dict[str, Point], rng: np.random.Generator, distort: bool
) -> tuple[dict[str, Point], float]:
    """World m -> paper mm (Y down) with a slight rotation, not-to-scale axes and jitter."""
    sx, sy = (rng.uniform(0.85, 1.15, 2) if distort else rng.uniform(0.95, 1.05, 2)).tolist()
    theta = math.radians(float(rng.uniform(-3.0, 3.0)))
    raw = {k: (sx * x, -sy * y) for k, (x, y) in pts.items()}
    rot = {
        k: (x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta))
        for k, (x, y) in raw.items()
    }
    xs, ys = [p[0] for p in rot.values()], [p[1] for p in rot.values()]
    mm_per_m = min(
        (SHEET_MM[0] - 2 * MARGIN_MM) / (max(xs) - min(xs)),
        (SHEET_MM[1] - 2 * MARGIN_MM) / (max(ys) - min(ys)),
    )
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    jitter = rng.normal(0.0, VERTEX_JITTER_MM, (len(rot), 2))
    paper = {
        k: (
            SHEET_MM[0] / 2 + (x - cx) * mm_per_m + float(jx),
            SHEET_MM[1] / 2 + (y - cy) * mm_per_m + float(jy),
        )
        for (k, (x, y)), (jx, jy) in zip(rot.items(), jitter, strict=True)
    }
    return paper, 1000.0 / mm_per_m


def _drawn_sketch(
    kind: str, rng: np.random.Generator, grid: bool, font: int
) -> tuple[np.ndarray, dict[str, Any]]:
    scene = _scene(kind, rng)
    paper, nominal_scale = _to_paper(scene["points"], rng, distort=kind == "schematic")
    width, height = round(SHEET_MM[0] * PX_PER_MM), round(SHEET_MM[1] * PX_PER_MM)
    gray = _paper(rng, width, height)
    _add_specks(rng, gray)
    sheet = np.repeat(gray[:, :, None], 3, axis=2)
    if grid:
        lines = np.zeros((height, width), np.uint8)
        for x in np.arange(0, width, GRID_PITCH_MM * PX_PER_MM):
            cv2.line(lines, (int(x), 0), (int(x), height), 255, 1)
        for y in np.arange(0, height, GRID_PITCH_MM * PX_PER_MM):
            cv2.line(lines, (0, int(y)), (width, int(y)), 255, 1)
        on = lines.astype(np.float32)[:, :, None] / 255.0
        sheet *= 1.0 - on * (1.0 - np.array(GRID_BGR_FACTOR, np.float32))
    ink = np.full((height, width), 240.0, np.float32)
    px = {k: (x * PX_PER_MM, y * PX_PER_MM) for k, (x, y) in paper.items()}
    centroid = (
        float(np.mean([p[0] for p in px.values()])),
        float(np.mean([p[1] for p in px.values()])),
    )
    segments: list[dict[str, Any]] = []
    for n, (role, p, q) in enumerate(scene["segments"], start=1):
        thickness = 3 if role in ("measure", "offset") else int(rng.integers(4, 6))
        stroke = TrueSegment(px[p], px[q], thickness, int(rng.integers(35, 100)), PX_PER_MM)
        _draw_stroke(rng, ink, stroke)
        segments.append(
            {
                "id": f"s{n}",
                "role": role,
                "points": [p, q],
                "paper_mm": _r([paper[p], paper[q]]),
                "image_px": _r([px[p], px[q]]),
                "thickness_px": thickness,
            }
        )
    world = scene["points"]
    dimensions: list[dict[str, Any]] = []
    for n, (p, q, segment_id, t, offset) in enumerate(scene["dimensions"], start=1):
        value = round(math.dist(world[p], world[q]), 2)
        (x0, y0), (x1, y1) = px[p], px[q]
        mid, length = (x0 + t * (x1 - x0), y0 + t * (y1 - y0)), math.dist(px[p], px[q])
        nx, ny = -(y1 - y0) / length, (x1 - x0) / length
        if (mid[0] - centroid[0]) * nx + (mid[1] - centroid[1]) * ny < 0:
            nx, ny = -nx, -ny
        angle = -math.degrees(math.atan2(y1 - y0, x1 - x0))
        angle = (angle + 90) % 180 - 90
        box = _ink_text(
            ink,
            written(value),
            (mid[0] + offset * nx, mid[1] + offset * ny),
            angle,
            font,
            int(rng.integers(35, 90)),
            rng,
        )
        dimensions.append(
            {
                "id": f"d{n}",
                "text": written(value),
                "value_m": value,
                "points": [p, q],
                "segment_id": segment_id,
                "text_box_px": box,
            }
        )
    for item in scene["evidence"]:
        x, y = px[item["id"]]
        cv2.circle(ink, (round(x), round(y)), 25, 60.0, 3, cv2.LINE_AA)
        item["label_box_px"] = _ink_text(ink, item["label"], (x + 50, y - 50), 0.0, font, 60, rng)
    for ref in ("A", "B"):
        x, y = px[ref]
        _ink_text(ink, ref, (x - 45, y + 45), 0.0, font, 60, rng)
    image = sheet * (ink / 240.0)[:, :, None]
    blur = float(rng.uniform(0.0, 0.7))
    image = cv2.GaussianBlur(image, (0, 0), sigmaX=blur) if blur > 0.05 else image
    truth: dict[str, Any] = {
        "rectified": True,
        "nominal_paper_scale": round(nominal_scale, 1),
        "world_frame": {"origin": "A", "x_axis": "A->B", "y": "up", "units": "m"},
        "grid": {"pitch_mm": GRID_PITCH_MM, "bgr_factor": list(GRID_BGR_FACTOR)} if grid else None,
        "points": [
            {"id": k, "world_m": _r(world[k], 6), "paper_mm": _r(paper[k]), "image_px": _r(px[k])}
            for k in world
        ],
        "segments": segments,
        "dimensions": dimensions,
        "evidence": scene["evidence"],
    }
    return np.clip(np.rint(image), 0, 255).astype(np.uint8), truth


def _pattern_photo(rng: np.random.Generator) -> tuple[np.ndarray, dict[str, Any]]:
    """A printed A4 pattern (known rectangle, dots, scale bar) photographed obliquely."""
    width, height = round(SHEET_MM[0] * PX_PER_MM), round(SHEET_MM[1] * PX_PER_MM)
    flat = np.full((height, width), 235, np.uint8)
    rw, rh = PATTERN_RECT_MM
    left, top = (SHEET_MM[0] - rw) / 2, (SHEET_MM[1] - rh) / 2
    rect = [(left, top), (left + rw, top), (left + rw, top + rh), (left, top + rh)]
    to_px = lambda p: (round(p[0] * PX_PER_MM), round(p[1] * PX_PER_MM))  # noqa: E731
    cv2.polylines(flat, [np.array([to_px(p) for p in rect], np.int32)], True, 20, 6, cv2.LINE_AA)
    dots = [
        (left + x, top + y)
        for x in np.arange(PATTERN_DOT_PITCH_MM, rw, PATTERN_DOT_PITCH_MM)
        for y in np.arange(PATTERN_DOT_PITCH_MM, rh, PATTERN_DOT_PITCH_MM)
    ]
    for dot in dots:
        cv2.circle(flat, to_px(dot), 15, 20, -1, cv2.LINE_AA)
    bar = [(left, top + rh + 10), (left + SCALE_BAR_MM, top + rh + 10)]
    cv2.line(flat, to_px(bar[0]), to_px(bar[1]), 20, 6, cv2.LINE_AA)
    sheet_mm = [(0.0, 0.0), (SHEET_MM[0], 0.0), SHEET_MM, (0.0, SHEET_MM[1])]
    pw, ph = PHOTO_SIZE
    base = [
        (0.15 * pw, 0.15 * ph),
        (0.85 * pw, 0.15 * ph),
        (0.85 * pw, 0.85 * ph),
        (0.15 * pw, 0.85 * ph),
    ]
    quad = [
        (x + float(dx), y + float(dy))
        for (x, y), (dx, dy) in zip(base, rng.uniform(-0.1, 0.1, (4, 2)) * pw, strict=True)
    ]
    paper_to_image = homography_from_4_points(sheet_mm, quad)
    to_canvas = paper_to_image @ np.diag([1 / PX_PER_MM, 1 / PX_PER_MM, 1.0])
    desk = np.zeros((ph, pw, 3), np.float32) + np.array(DESK_BGR, np.float32)
    photo = cv2.warpPerspective(cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR), to_canvas, PHOTO_SIZE)
    covered = cv2.warpPerspective(np.full((height, width), 255, np.uint8), to_canvas, PHOTO_SIZE)
    alpha = covered.astype(np.float32)[:, :, None] / 255.0
    image = cv2.GaussianBlur(
        desk * (1 - alpha) + photo.astype(np.float32) * alpha,
        (0, 0),
        sigmaX=float(rng.uniform(0.3, 0.7)),
    )
    image += rng.normal(0, 3, image.shape).astype(np.float32)  # sensor noise
    image_px = lambda pts: _r(apply_h(paper_to_image, pts).tolist())  # noqa: E731
    edges = list(zip(rect, rect[1:] + rect[:1], strict=True)) + [tuple(bar)]
    truth: dict[str, Any] = {
        "rectified": False,
        "homography": {
            "paper_mm_to_image_px": homography_to_json(paper_to_image),
            "image_px_to_paper_mm": homography_to_json(invert_h(paper_to_image)),
        },
        "pattern": {
            "sheet_mm": list(SHEET_MM),
            "sheet_corners_paper_mm": _r(sheet_mm),
            "sheet_corners_image_px": image_px(sheet_mm),
            "rect_width_mm": rw,
            "rect_height_mm": rh,
            "rect_corners_paper_mm": _r(rect),
            "rect_corners_image_px": image_px(rect),
            "dot_pitch_mm": PATTERN_DOT_PITCH_MM,
            "dots_paper_mm": _r(dots),
            "dots_image_px": image_px(dots),
            "scale_bar_mm": SCALE_BAR_MM,
        },
        "segments": [
            {"id": f"s{n}", "role": "pattern", "paper_mm": _r([p, q]), "image_px": image_px([p, q])}
            for n, (p, q) in enumerate(edges, start=1)
        ],
        "points": [],
        "dimensions": [],
        "evidence": [],
    }
    return np.clip(np.rint(image), 0, 255).astype(np.uint8), truth


def generate_one(
    seed: int, index: int, grid_mode: str, font_name: str
) -> tuple[np.ndarray, dict[str, Any]]:
    """Image ``index`` (1-based) of the set of ``seed`` and its ground truth."""
    rng = np.random.default_rng([seed, index])
    kind = KINDS[(index - 1) % len(KINDS)]
    grid = grid_mode == "on" or (grid_mode == "mixed" and ((index - 1) // len(KINDS)) % 2 == 1)
    if kind == "oblique_pattern":
        image, truth = _pattern_photo(rng)
        grid = False
    else:
        image, truth = _drawn_sketch(kind, rng, grid, FONTS[font_name])
    header = {
        "schema": SCHEMA,
        "generator": "validation/synthetic/generate.py",
        "seed": seed,
        "index": index,
        "kind": kind,
        "image": f"{index:02d}-{kind}.jpg",
        "size_px": [int(image.shape[1]), int(image.shape[0])],
        "px_per_mm": PX_PER_MM,
        "font": None if kind == "oblique_pattern" else font_name,
    }
    return image, {**header, **truth}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic validation set.")
    parser.add_argument("--count", type=int, default=20, help="number of images (default 20)")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "output")
    parser.add_argument("--seed", type=int, default=115, help="base seed (default 115)")
    parser.add_argument("--grid", choices=("mixed", "on", "off"), default="mixed")
    parser.add_argument("--font", choices=sorted(FONTS), default="plain")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be at least 1")
    args.out.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for i in range(1, args.count + 1):
        image, truth = generate_one(args.seed, i, args.grid, args.font)
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError(f"JPEG encoding failed for image {i}")
        (args.out / truth["image"]).write_bytes(jpeg.tobytes())
        stem = truth["image"].removesuffix(".jpg")
        text = json.dumps(truth, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        (args.out / f"{stem}.json").write_text(text, encoding="utf-8")
        index.append({"image": truth["image"], "truth": f"{stem}.json", "kind": truth["kind"]})
    listing = {"schema": SCHEMA, "seed": args.seed, "count": args.count, "items": index}
    (args.out / "index.json").write_text(json.dumps(listing, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.count} images with ground truth to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
