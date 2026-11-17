// Client-side preview of the triangulation and baseline-offset tools (spec 11.3, US-08, US-09):
// a pure port of the server's `sketch2cad.geometry.metrics` (circle_intersections, triangulate,
// baseline_offset_point) with the same tolerance, candidate order and "nearest" rule, so that the
// markers drawn before "Aplicar" are where the server will put the point. The server stays
// authoritative: it recomputes everything and its 422 (V-06, V-07) wins.
//
// World metres, Y up: "left" of A→B is its counter-clockwise normal.
import type { Point2 } from "../store/types";

/** Centre-distance tolerance under which two circles are tangent (server TANGENCY_TOLERANCE_M). */
export const TANGENCY_TOLERANCE_M = 1e-9;

export type Solution = "nearest" | "alt";
export type Side = "left" | "right";

export interface TriangulationPreview {
  /** 0 (circles do not cross, V-06), 1 (tangent) or 2 points, the first LEFT of A→B. */
  candidates: Point2[];
  /** Index of the candidate closest to the hint (tie or no hint: 0); null without candidates. */
  nearestIndex: number | null;
  /** Candidate picked by the solution; null without candidates. */
  point: Point2 | null;
}

function tangentPosition(d: number, ra: number, rb: number): number | null {
  let gap = d - (ra + rb);
  if (Math.abs(gap) <= TANGENCY_TOLERANCE_M) return ra + gap / 2;
  gap = d - Math.abs(ra - rb);
  if (Math.abs(gap) > TANGENCY_TOLERANCE_M) return null;
  return ra >= rb ? ra + gap / 2 : -(ra - gap / 2);
}

/** Points at distance `ra` from `a` and `rb` from `b` (0, 1 or 2; two: left then right). */
export function circleIntersections(a: Point2, ra: number, b: Point2, rb: number): Point2[] {
  const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (d <= TANGENCY_TOLERANCE_M) return [];
  if (d > ra + rb + TANGENCY_TOLERANCE_M) return [];
  if (d < Math.abs(ra - rb) - TANGENCY_TOLERANCE_M) return [];
  const ux = (b[0] - a[0]) / d;
  const uy = (b[1] - a[1]) / d;
  const tangentAt = tangentPosition(d, ra, rb);
  if (tangentAt !== null) return [[a[0] + tangentAt * ux, a[1] + tangentAt * uy]];
  const x = (d * d + (ra - rb) * (ra + rb)) / (2 * d);
  const foot: Point2 = [a[0] + x * ux, a[1] + x * uy];
  // Heron form of the half chord, as on the server (ra² − x² cancels badly for thin triangles).
  const product = (ra + rb + d) * (rb - ra + d) * (ra - rb + d) * (ra + rb - d);
  const h = Math.sqrt(Math.max(product, 0)) / (2 * d);
  return [
    [foot[0] - h * uy, foot[1] + h * ux],
    [foot[0] + h * uy, foot[1] - h * ux],
  ];
}

/** Candidates for P at `dA` from A and `dB` from B; `hint` is the current position of P. */
export function triangulate(
  a: Point2,
  b: Point2,
  dA: number,
  dB: number,
  solution: Solution = "nearest",
  hint: Point2 | null = null,
): TriangulationPreview {
  const candidates = circleIntersections(a, dA, b, dB);
  const [left, right] = candidates;
  if (left === undefined) return { candidates, nearestIndex: null, point: null };
  let nearest = 0;
  if (hint !== null && right !== undefined) {
    const toRight = Math.hypot(right[0] - hint[0], right[1] - hint[1]);
    nearest = toRight < Math.hypot(left[0] - hint[0], left[1] - hint[1]) ? 1 : 0;
  }
  const index = solution === "nearest" || right === undefined ? nearest : 1 - nearest;
  return { candidates, nearestIndex: nearest, point: candidates[index] ?? null };
}

/** `A + s·û + d·n̂`, or null when A and B coincide (the baseline has no direction). */
export function baselineOffsetPoint(
  a: Point2,
  b: Point2,
  s: number,
  d: number,
  side: Side,
): Point2 | null {
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (length === 0) return null;
  const ux = (b[0] - a[0]) / length;
  const uy = (b[1] - a[1]) / length;
  const [nx, ny] = side === "left" ? [-uy, ux] : [uy, -ux];
  return [a[0] + s * ux + d * nx, a[1] + s * uy + d * ny];
}
