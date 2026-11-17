"""Joining of collinear fragments and straightening of small kinks (``suggest_lines``).

A hand-drawn wall often comes out of the skeleton in pieces: the stroke has a short gap,
the pen lifted and restarted a little to the side, or a junction was too wobbly for the
branches to be paired there. Two fragments are joined end to end when:

* their ends are at most ``gap_px`` apart (an overlap of up to ``gap_px`` also counts);
* their end directions (the terminal segment of each simplified polyline, pointing
  outwards) are opposite within ``angle_deg``, i.e. one continues the other;
* each end lies at most ``offset_px`` from the line of the other end (lateral offset).

First, a polyline is split at a corner vertex (a turn of ``angle_deg`` or more) when the
end of another fragment is within ``gap_px`` of that vertex, collinear with one of its two
segments and within ``offset_px`` of its line: a wall broken just at a T junction was
traced as an L (wall piece + stem through a 2-way node), and the through-wall must win.
Candidates are taken greedily, smallest gap first (then angle, then index), each end
joins at most once and no chain is closed into a loop, so the result is deterministic.
The join vertex is the midpoint of the two ends.

Then every open polyline is straightened: an interior vertex is dropped while every
original vertex between its two neighbours stays within ``offset_px`` of the segment that
replaces them, smallest deviation first. A wall drawn with a slight bend or wobble becomes
one segment; corners whose arms leave the line by more than ``offset_px`` are kept.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sketch2cad.vision.simplify import PointXY

_Pixel = tuple[int, int]


@dataclass(frozen=True)
class Fragment:
    """A simplified polyline and the pixel path it came from (for its straightness)."""

    points: list[PointXY]
    pixels: list[_Pixel]

    @property
    def closed(self) -> bool:
        return len(self.points) > 2 and self.points[0] == self.points[-1]

    def reversed(self) -> "Fragment":
        return Fragment(self.points[::-1], self.pixels[::-1])


def _outward(points: Sequence[PointXY], at_start: bool) -> tuple[PointXY, PointXY] | None:
    """End point and unit direction leaving the polyline at that end (``None`` if degenerate)."""
    end, inner = (points[0], points[1]) if at_start else (points[-1], points[-2])
    dx, dy = end[0] - inner[0], end[1] - inner[1]
    norm = math.hypot(dx, dy)
    return None if norm == 0 else (end, (dx / norm, dy / norm))


def _lateral(point: PointXY, origin: PointXY, direction: PointXY) -> float:
    """Distance from ``point`` to the line through ``origin`` along ``direction``."""
    return abs((point[0] - origin[0]) * direction[1] - (point[1] - origin[1]) * direction[0])


def _ends(fragments: Sequence[Fragment]) -> list[tuple[int, bool, PointXY, PointXY]]:
    """``(fragment index, at start, end point, outward direction)`` of every open fragment."""
    ends: list[tuple[int, bool, PointXY, PointXY]] = []
    for index, fragment in enumerate(fragments):
        if fragment.closed or len(fragment.points) < 2:
            continue
        for at_start in (True, False):
            found = _outward(fragment.points, at_start)
            if found is not None:
                ends.append((index, at_start, found[0], found[1]))
    return ends


def _unit(start: PointXY, end: PointXY) -> PointXY | None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    norm = math.hypot(dx, dy)
    return None if norm == 0 else (dx / norm, dy / norm)


def _claimed(
    vertex: PointXY,
    neighbours: tuple[PointXY, PointXY],
    end: tuple[PointXY, PointXY],
    gap_px: float,
    offset_px: float,
    angle_deg: float,
) -> bool:
    """Whether the end ``(point, outward direction)`` continues into one segment at ``vertex``."""
    point, direction = end
    if math.dist(point, vertex) > gap_px or _lateral(vertex, point, direction) > offset_px:
        return False
    for neighbour in neighbours:
        away = _unit(vertex, neighbour)
        if away is None:
            continue
        cosine = max(-1.0, min(1.0, direction[0] * away[0] + direction[1] * away[1]))
        if math.degrees(math.acos(cosine)) < angle_deg:
            return True
    return False


def _split_claimed_corners(
    fragments: Sequence[Fragment], gap_px: float, offset_px: float, angle_deg: float
) -> list[Fragment]:
    ends = _ends(fragments)
    result: list[Fragment] = []
    for index, fragment in enumerate(fragments):
        points = fragment.points
        cuts: list[int] = []
        if not fragment.closed:
            for k in range(1, len(points) - 1):
                if _turn(points[k - 1], points[k], points[k + 1]) < angle_deg:
                    continue
                neighbours = (points[k - 1], points[k + 1])
                if any(
                    other != index
                    and _claimed(points[k], neighbours, (p, d), gap_px, offset_px, angle_deg)
                    for other, _, p, d in ends
                ):
                    cuts.append(k)
        if not cuts:
            result.append(fragment)
            continue
        start_point, start_pixel = 0, 0
        for k in cuts:
            pixel = _nearest_pixel(fragment.pixels, points[k], start_pixel)
            result.append(
                Fragment(points[start_point : k + 1], fragment.pixels[start_pixel : pixel + 1])
            )
            start_point, start_pixel = k, pixel
        result.append(Fragment(points[start_point:], fragment.pixels[start_pixel:]))
    return result


def _nearest_pixel(pixels: Sequence[_Pixel], point: PointXY, start: int) -> int:
    """Index (from ``start`` on) of the pixel of ``pixels`` closest to ``point``."""
    best = min(range(start, len(pixels)), key=lambda i: math.dist(pixels[i], point))
    return best


def _candidates(
    fragments: Sequence[Fragment], gap_px: float, offset_px: float, angle_deg: float
) -> list[tuple[float, float, int, bool, int, bool]]:
    ends = _ends(fragments)
    found_pairs: list[tuple[float, float, int, bool, int, bool]] = []
    for a in range(len(ends)):
        ia, sa, pa, da = ends[a]
        for b in range(a + 1, len(ends)):
            ib, sb, pb, db = ends[b]
            if ia == ib:
                continue
            gap = math.dist(pa, pb)
            if gap > gap_px:
                continue
            cosine = max(-1.0, min(1.0, -(da[0] * db[0] + da[1] * db[1])))
            turn = math.degrees(math.acos(cosine))
            if turn >= angle_deg:
                continue
            ahead = (pb[0] - pa[0]) * da[0] + (pb[1] - pa[1]) * da[1]
            if ahead < -gap_px:
                continue
            if _lateral(pb, pa, da) > offset_px or _lateral(pa, pb, db) > offset_px:
                continue
            found_pairs.append((gap, turn, ia, sa, ib, sb))
    return sorted(found_pairs)


def join_fragments(
    fragments: Sequence[Fragment], *, gap_px: float, offset_px: float, angle_deg: float
) -> list[Fragment]:
    """Fragments chained end to end where one continues the other (see the module docstring)."""
    fragments = _split_claimed_corners(fragments, gap_px, offset_px, angle_deg)
    root = list(range(len(fragments)))

    def find(node: int) -> int:
        while root[node] != node:
            root[node] = root[root[node]]
            node = root[node]
        return node

    partner: dict[tuple[int, bool], tuple[int, bool]] = {}
    for _, _, ia, sa, ib, sb in _candidates(fragments, gap_px, offset_px, angle_deg):
        if (ia, sa) in partner or (ib, sb) in partner or find(ia) == find(ib):
            continue
        partner[(ia, sa)], partner[(ib, sb)] = (ib, sb), (ia, sa)
        root[find(ib)] = find(ia)
    if not partner:
        return fragments
    used = [False] * len(fragments)
    joined: list[Fragment] = []
    for index, fragment in enumerate(fragments):
        if used[index] or ((index, True) in partner and (index, False) in partner):
            continue
        # A chain start: an end without partner (closed fragments have none either).
        forward = (index, True) not in partner
        chain = fragment if forward else fragment.reversed()
        used[index] = True
        current, at_start = index, not forward
        while (nxt := partner.get((current, at_start))) is not None:
            other, other_start = nxt
            piece = fragments[other] if other_start else fragments[other].reversed()
            chain = _concatenate(chain, piece)
            used[other] = True
            current, at_start = other, not other_start
        joined.append(chain)
    return joined


def _concatenate(first: Fragment, second: Fragment) -> Fragment:
    a, b = first.points[-1], second.points[0]
    middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    points = [*first.points[:-1], middle, *second.points[1:]]
    pixels = (
        first.pixels + second.pixels[1:]
        if first.pixels[-1:] == second.pixels[:1]
        else (first.pixels + second.pixels)
    )
    return Fragment(points, pixels)


def _turn(a: PointXY, b: PointXY, c: PointXY) -> float:
    """Turning angle in degrees at ``b`` of the path ``a -> b -> c`` (0 when straight)."""
    first = math.atan2(b[1] - a[1], b[0] - a[0])
    second = math.atan2(c[1] - b[1], c[0] - b[0])
    return abs(math.degrees((second - first + math.pi) % (2 * math.pi) - math.pi))


def _deviation(points: Sequence[PointXY], start: PointXY, end: PointXY) -> float:
    length = math.dist(start, end)
    if length == 0:
        return max((math.dist(p, start) for p in points), default=0.0)
    direction = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
    return max((_lateral(p, start, direction) for p in points), default=0.0)


def straighten(points: Sequence[PointXY], *, offset_px: float) -> list[PointXY]:
    """Drop the kinks of an open polyline (see the module docstring); closed ones unchanged."""
    kept = list(range(len(points)))
    if len(points) < 3 or points[0] == points[-1]:
        return list(points)
    while len(kept) > 2:
        best: tuple[float, int] | None = None
        for k in range(1, len(kept) - 1):
            a, c = points[kept[k - 1]], points[kept[k + 1]]
            deviation = _deviation(points[kept[k - 1] + 1 : kept[k + 1]], a, c)
            if deviation <= offset_px and (best is None or deviation < best[0]):
                best = (deviation, k)
        if best is None:
            break
        del kept[best[1]]
    return [points[i] for i in kept]
