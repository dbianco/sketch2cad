"""Skeleton graph of a stroke mask, traced into ordered pixel paths.

``skimage.morphology.skeletonize`` thins the mask to 1 px lines. The graph is traced here
instead of with ``sknw`` (unmaintained since 2022 and pulling numba/llvmlite):

* nodes are 8-connected clusters of skeleton pixels whose neighbour count is not 2
  (endpoints have 1, junctions 3 or more, isolated pixels 0 and are ignored);
* edges are the runs of 2-neighbour pixels between nodes, including the node pixel at
  each end; closed loops without any node are traced as closed paths;
* spurs (an edge from an endpoint to a junction) and self-loops shorter than the minimum
  length are pruned until none is left;
* edges are joined into paths through every node of degree 2 (a pass-through, typically a
  corner whose spur was pruned) and, at junctions of degree 3 or more, through the pairs
  of branches whose directions are collinear within ``merge_angle_deg`` (closest first),
  so a straight stroke crossed by another stays one path.

Paths are lists of ``(x, y)`` pixel coordinates (column, row; integers are pixel
centres); a closed path repeats its first point at the end. Output order is deterministic.
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, cast

import cv2
import numpy as np
import numpy.typing as npt
from skimage.morphology import skeletonize  # pyright: ignore[reportUnknownVariableType]

from sketch2cad.vision.binarize import Mask

PathXY = list[tuple[int, int]]
"""Ordered ``(x, y)`` pixel coordinates of a path."""

DIRECTION_SPAN_PX = 20
"""Pixels from a node used to estimate the direction of a branch (2 mm at 10 px/mm)."""

_OFFSETS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
_Pixel = tuple[int, int]
_End = tuple[int, bool]
"""Edge index and whether the end is the start of the edge."""


@dataclass(frozen=True)
class _Edge:
    start: int
    end: int
    pixels: list[_Pixel]
    """``(row, col)`` in the padded image, from the start node pixel to the end node pixel."""


def pixel_length(pixels: list[tuple[int, int]]) -> float:
    """Length of a pixel path (1 per straight step, sqrt(2) per diagonal step)."""
    if len(pixels) < 2:
        return 0.0
    steps = np.diff(np.asarray(pixels, dtype=np.float64), axis=0)
    return float(np.hypot(steps[:, 0], steps[:, 1]).sum())


def skeleton_paths(mask: Mask, *, min_length_px: float, merge_angle_deg: float) -> list[PathXY]:
    """Ordered pixel paths of the skeleton of ``mask`` (see the module docstring)."""
    if not mask.any():
        return []
    thin: npt.NDArray[np.bool_] = np.asarray(skeletonize(mask), dtype=np.bool_)  # pyright: ignore[reportUnknownArgumentType]
    skeleton = np.pad(thin, 1)
    ink = skeleton.astype(np.uint8)
    ring = np.ones((3, 3), dtype=np.float32)
    ring[1, 1] = 0
    neighbours = cv2.filter2D(ink, -1, ring, borderType=cv2.BORDER_CONSTANT)
    nodes = (skeleton & (neighbours != 2)).astype(np.uint8)
    found = cast(Any, cv2.connectedComponents(nodes, connectivity=8, ltype=cv2.CV_32S))
    labels: npt.NDArray[np.int32] = np.asarray(found[1], dtype=np.int32)
    visited = np.zeros(skeleton.shape, dtype=np.bool_)
    edges = _prune(_trace_edges(skeleton, labels, visited), min_length_px)
    paths = _join(edges, merge_angle_deg) + _trace_loops(skeleton, visited)
    return [[(col - 1, row - 1) for row, col in path] for path in paths]


def _next_pixel(skeleton: npt.NDArray[np.bool_], cur: _Pixel, prev: _Pixel) -> _Pixel | None:
    for dr, dc in _OFFSETS:
        candidate = (cur[0] + dr, cur[1] + dc)
        if candidate != prev and skeleton[candidate]:
            return candidate
    return None


def _trace_edges(
    skeleton: npt.NDArray[np.bool_], labels: npt.NDArray[np.int32], visited: npt.NDArray[np.bool_]
) -> list[_Edge]:
    edges: list[_Edge] = []
    for row, col in np.argwhere(labels > 0).tolist():
        origin = (row, col)
        for dr, dc in _OFFSETS:
            first = (row + dr, col + dc)
            if not skeleton[first] or labels[first] or visited[first]:
                continue
            pixels = [origin, first]
            visited[first] = True
            prev, cur = origin, first
            while (nxt := _next_pixel(skeleton, cur, prev)) is not None:
                pixels.append(nxt)
                if labels[nxt] or visited[nxt]:
                    break
                visited[nxt] = True
                prev, cur = cur, nxt
            end = int(labels[pixels[-1]])
            if end:
                edges.append(_Edge(int(labels[origin]), end, pixels))
    return edges


def _trace_loops(
    skeleton: npt.NDArray[np.bool_], visited: npt.NDArray[np.bool_]
) -> list[list[_Pixel]]:
    """Closed paths of the skeleton components that have no node (all pixels degree 2)."""
    loops: list[list[_Pixel]] = []
    for row, col in np.argwhere(skeleton & ~visited).tolist():
        start = (row, col)
        if visited[start]:
            continue
        visited[start] = True
        pixels = [start]
        prev, cur = start, start
        while (nxt := _next_pixel(skeleton, cur, prev)) is not None:
            if nxt == start and len(pixels) > 2:
                pixels.append(start)
                break
            if visited[nxt]:
                break
            visited[nxt] = True
            pixels.append(nxt)
            prev, cur = cur, nxt
        loops.append(pixels)
    return loops


def _prune(edges: list[_Edge], min_length_px: float) -> list[_Edge]:
    """Drop short spurs (endpoint to junction) and short self-loops until none is left."""
    lengths = [pixel_length(edge.pixels) for edge in edges]
    alive = list(range(len(edges)))
    while True:
        degree = Counter(node for i in alive for node in (edges[i].start, edges[i].end))
        keep: list[int] = []
        for i in alive:
            edge = edges[i]
            short = lengths[i] < min_length_px
            self_loop = edge.start == edge.end
            spur = (degree[edge.start] == 1 and degree[edge.end] >= 3) or (
                degree[edge.end] == 1 and degree[edge.start] >= 3
            )
            if not (short and (self_loop or spur)):
                keep.append(i)
        if len(keep) == len(alive):
            return [edges[i] for i in alive]
        alive = keep


def _direction(edge: _Edge, at_start: bool) -> tuple[float, float] | None:
    """Unit ``(row, col)`` direction of the edge leaving its node at the given end."""
    pixels = edge.pixels if at_start else edge.pixels[::-1]
    far = pixels[min(len(pixels) - 1, DIRECTION_SPAN_PX)]
    dr, dc = far[0] - pixels[0][0], far[1] - pixels[0][1]
    norm = math.hypot(dr, dc)
    return None if norm == 0 else (dr / norm, dc / norm)


def _pairs(edges: list[_Edge], merge_angle_deg: float) -> dict[_End, _End]:
    """Edge ends joined through each node (pass-through or collinear branches)."""
    by_node: dict[int, list[_End]] = defaultdict(list)
    for i, edge in enumerate(edges):
        by_node[edge.start].append((i, True))
        by_node[edge.end].append((i, False))
    partner: dict[_End, _End] = {}
    for node in sorted(by_node):
        ends = by_node[node]
        if len(ends) == 2:
            partner[ends[0]], partner[ends[1]] = ends[1], ends[0]
            continue
        directions = [_direction(edges[i], at_start) for i, at_start in ends]
        candidates: list[tuple[float, int, int]] = []
        for a in range(len(ends)):
            for b in range(a + 1, len(ends)):
                da, db = directions[a], directions[b]
                if da is None or db is None:
                    continue
                cosine = max(-1.0, min(1.0, -(da[0] * db[0] + da[1] * db[1])))
                deviation = math.degrees(math.acos(cosine))
                if deviation < merge_angle_deg:
                    candidates.append((deviation, a, b))
        for _, a, b in sorted(candidates):
            if ends[a] not in partner and ends[b] not in partner:
                partner[ends[a]], partner[ends[b]] = ends[b], ends[a]
    return partner


def _join(edges: list[_Edge], merge_angle_deg: float) -> list[list[_Pixel]]:
    """Chain the edges through their paired ends; chains with no free end are closed."""
    partner = _pairs(edges, merge_angle_deg)
    used = [False] * len(edges)
    paths: list[list[_Pixel]] = []

    def follow(index: int, forward: bool) -> list[_Pixel]:
        pixels: list[_Pixel] = []
        while not used[index]:
            used[index] = True
            run = edges[index].pixels if forward else edges[index].pixels[::-1]
            pixels.extend(run[1:] if pixels and pixels[-1] == run[0] else run)
            nxt = partner.get((index, not forward))
            if nxt is None:
                break
            index, forward = nxt[0], nxt[1]
        return pixels

    for i in range(len(edges)):
        if not used[i] and (i, True) not in partner:
            paths.append(follow(i, True))
        elif not used[i] and (i, False) not in partner:
            paths.append(follow(i, False))
    for i in range(len(edges)):
        if not used[i]:
            closed = follow(i, True)
            if closed[-1] != closed[0]:
                closed.append(closed[0])
            paths.append(closed)
    return paths
