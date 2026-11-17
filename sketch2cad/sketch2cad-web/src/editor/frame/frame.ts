// Local coordinate frame of a plan (spec section 11.1), mirroring the API's geometry/frame.py and
// geometry/transforms.py so that the client and the server agree on every conversion.
//
// Paper: millimetres, Y down. World: metres, Y up, `m = mm * paperScale / 1000` with Y negated.
// The frame's origin is a paper point; its +X axis makes `x_axis_angle_deg` with the world +X
// axis (counter-clockwise). Entity coordinates (`coords_m`), and so the editor canvas, are LOCAL
// coordinates of the current frame: `local = R(-theta) (world - origin_world)`. Before any frame
// is set the server uses DEFAULT_FRAME (paper origin, world axes), where local equals world.
// `north_angle_deg` is measured counter-clockwise from the local +Y axis and never moves entities.
import type { components } from "../../api/schema";
import type { Point2 } from "../store/types";

export type LocalFrame = components["schemas"]["LocalFrameOut"];

/** Paper scale of plans without a declared one (spec section 10), as the API uses it. */
export const PROVISIONAL_PAPER_SCALE = 100;

const FULL_TURN_DEG = 360;

/** Frame of coordinates drawn before any local frame is set. */
export const DEFAULT_FRAME: LocalFrame = {
  origin_paper_mm: [0, 0],
  x_axis_angle_deg: 0,
  north_angle_deg: 0,
};

/** The declared paper scale denominator, or the provisional 1:100 when there is none. */
export function effectivePaperScale(paperScale: number | null | undefined): number {
  return paperScale ?? PROVISIONAL_PAPER_SCALE;
}

/** `angleDeg` reduced to [0, 360); throws a RangeError when it is not finite. */
export function normalizeDeg(angleDeg: number): number {
  if (!Number.isFinite(angleDeg)) throw new RangeError(`Angle must be finite, got ${angleDeg}`);
  const reduced = ((angleDeg % FULL_TURN_DEG) + FULL_TURN_DEG) % FULL_TURN_DEG;
  return reduced === FULL_TURN_DEG || reduced === 0 ? 0 : reduced;
}

/** Cosine and sine of `angleDeg`, exact for multiples of 90 degrees (as the server does). */
function cosSin(angleDeg: number): [number, number] {
  const normalized = normalizeDeg(angleDeg);
  if (normalized % 90 === 0) {
    const quarters: [number, number][] = [
      [1, 0],
      [0, 1],
      [-1, 0],
      [0, -1],
    ];
    return quarters[normalized / 90] as [number, number];
  }
  const radians = (angleDeg * Math.PI) / 180;
  return [Math.cos(radians), Math.sin(radians)];
}

function rotate(point: Point2, angleDeg: number): Point2 {
  const [c, s] = cosSin(angleDeg);
  return [c * point[0] - s * point[1], s * point[0] + c * point[1]];
}

/** Paper mm (Y down) to world metres (Y up). */
export function paperToWorld(mm: Point2, paperScale: number): Point2 {
  const k = paperScale / 1000;
  return [mm[0] * k, -mm[1] * k];
}

/** World metres (Y up) to paper mm (Y down); inverse of `paperToWorld`. */
export function worldToPaper(m: Point2, paperScale: number): Point2 {
  const k = 1000 / paperScale;
  return [m[0] * k, -m[1] * k];
}

/** World point of local coordinates `p` of `frame`: `origin + R(theta) p`. */
export function localToWorld(frame: LocalFrame, paperScale: number, p: Point2): Point2 {
  const origin = paperToWorld(frame.origin_paper_mm, paperScale);
  const turned = rotate(p, frame.x_axis_angle_deg);
  return [origin[0] + turned[0], origin[1] + turned[1]];
}

/** Local coordinates in `frame` of world point `w`: `R(-theta) (w - origin)`. */
export function worldToLocal(frame: LocalFrame, paperScale: number, w: Point2): Point2 {
  const origin = paperToWorld(frame.origin_paper_mm, paperScale);
  return rotate([w[0] - origin[0], w[1] - origin[1]], -frame.x_axis_angle_deg);
}

/** Paper point (mm) under local coordinates `p` of `frame`, e.g. a click on the canvas. */
export function localToPaper(frame: LocalFrame, paperScale: number, p: Point2): Point2 {
  return worldToPaper(localToWorld(frame, paperScale, p), paperScale);
}

/** Direction from `from` to `to` in degrees, counter-clockwise from +X, in [0, 360). */
export function directionDeg(from: Point2, to: Point2): number {
  return normalizeDeg((Math.atan2(to[1] - from[1], to[0] - from[0]) * 180) / Math.PI);
}

/** World angle of the +X axis of a new frame whose +X points at `localAngleDeg` in `frame`. */
export function worldAxisAngle(frame: LocalFrame, localAngleDeg: number): number {
  return normalizeDeg(frame.x_axis_angle_deg + localAngleDeg);
}

/** Whether both frames share origin and +X axis (they may differ only in North). */
export function samePlacement(a: LocalFrame, b: LocalFrame): boolean {
  return (
    a.origin_paper_mm[0] === b.origin_paper_mm[0] &&
    a.origin_paper_mm[1] === b.origin_paper_mm[1] &&
    a.x_axis_angle_deg === b.x_axis_angle_deg
  );
}

/** `p' = R(theta) p + (dx, dy)`, as the API's RigidTransform. */
export interface RigidTransform {
  dx_m: number;
  dy_m: number;
  theta_deg: number;
}

export function applyRigid(transform: RigidTransform, p: Point2): Point2 {
  const turned = rotate(p, transform.theta_deg);
  return [turned[0] + transform.dx_m, turned[1] + transform.dy_m];
}

/** Transform taking local coordinates in `from` to local coordinates in `to` (server's rule). */
export function rigidBetween(from: LocalFrame, to: LocalFrame, paperScale: number): RigidTransform {
  const raw = from.x_axis_angle_deg - to.x_axis_angle_deg;
  // IEEE remainder like Python's math.remainder: result in [-180, 180].
  const theta = raw - FULL_TURN_DEG * Math.round(raw / FULL_TURN_DEG);
  const a = paperToWorld(from.origin_paper_mm, paperScale);
  const b = paperToWorld(to.origin_paper_mm, paperScale);
  const [dx, dy] = rotate([a[0] - b[0], a[1] - b[1]], -to.x_axis_angle_deg);
  return { dx_m: dx + 0, dy_m: dy + 0, theta_deg: theta + 0 };
}
