import fc from "fast-check";
import { describe, expect, it } from "vitest";
import type { Point2 } from "../store/types";
import {
  DEFAULT_FRAME,
  applyRigid,
  directionDeg,
  effectivePaperScale,
  localToPaper,
  localToWorld,
  normalizeDeg,
  paperToWorld,
  rigidBetween,
  samePlacement,
  worldAxisAngle,
  worldToLocal,
  worldToPaper,
  type LocalFrame,
} from "./frame";

// Frames and expectations of the API's tests/plans/test_local_frame.py.
const FIRST: LocalFrame = { origin_paper_mm: [12.5, 30], x_axis_angle_deg: 0, north_angle_deg: 0 };
const MOVED: LocalFrame = {
  origin_paper_mm: [80.25, 140.5],
  x_axis_angle_deg: 33.7,
  north_angle_deg: 15,
};

const finite = fc.double({ min: -1e3, max: 1e3, noNaN: true });
const angle = fc.double({ min: -720, max: 720, noNaN: true });
const point = fc.tuple(finite, finite) as fc.Arbitrary<Point2>;
const scale = fc.constantFrom(50, 100, 500);
const frame: fc.Arbitrary<LocalFrame> = fc.record({
  origin_paper_mm: point,
  x_axis_angle_deg: angle.map(normalizeDeg),
  north_angle_deg: fc.constant(0),
});

/** Independent local -> world map, as the server test writes it: `o + R(theta) p`. */
function world(f: LocalFrame, local: Point2, paperScale: number): Point2 {
  const ox = (f.origin_paper_mm[0] * paperScale) / 1000;
  const oy = (-f.origin_paper_mm[1] * paperScale) / 1000;
  const t = (f.x_axis_angle_deg * Math.PI) / 180;
  return [
    ox + Math.cos(t) * local[0] - Math.sin(t) * local[1],
    oy + Math.sin(t) * local[0] + Math.cos(t) * local[1],
  ];
}

function dist(a: Point2, b: Point2): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

describe("frame conversions", () => {
  it("normalizes angles to [0, 360) like the server and rejects non-finite ones", () => {
    expect([-90, 360, 725.5, -1e-20, -0].map(normalizeDeg)).toEqual([270, 0, 5.5, 0, 0]);
    expect(() => normalizeDeg(Number.NaN)).toThrow(RangeError);
  });

  it("maps paper mm to world metres with the paper scale and Y inverted", () => {
    expect(paperToWorld([12.5, 30], 100)).toEqual([1.25, -3]);
    expect(worldToPaper([1.25, -3], 100)).toEqual([12.5, 30]);
    // World placement of the server's frame (10 mm, 20 mm) at 1:100.
    expect(localToWorld({ ...DEFAULT_FRAME, origin_paper_mm: [10, 20] }, 100, [0, 0])).toEqual([
      1, -2,
    ]);
    expect(effectivePaperScale(null)).toBe(100);
    expect(effectivePaperScale(250)).toBe(250);
  });

  it("matches the server's first-frame example: transform and moved line", () => {
    const transform = rigidBetween(DEFAULT_FRAME, FIRST, 100);
    expect(transform).toEqual({ dx_m: -1.25, dy_m: 3, theta_deg: 0 });
    const line: Point2[] = [
      [1.25, -3],
      [4, -3],
    ];
    expect(line.map((p) => applyRigid(transform, p))).toEqual([
      [0, 0],
      [2.75, 0],
    ]);
  });

  it("matches the server's moved-frame rotation and exact quarter turns", () => {
    expect(rigidBetween(FIRST, MOVED, 100).theta_deg).toBeCloseTo(-33.7, 9);
    expect(applyRigid({ dx_m: 1, dy_m: 2, theta_deg: 90 }, [3, 4])).toEqual([-3, 5]);
    const placed: LocalFrame = { origin_paper_mm: [10, 20], x_axis_angle_deg: 45, north_angle_deg: 0 };
    expect(rigidBetween(placed, placed, 100)).toEqual({ dx_m: 0, dy_m: 0, theta_deg: 0 });
  });

  it("keeps world positions when moving local coordinates between frames", () => {
    fc.assert(
      fc.property(frame, frame, fc.array(point, { minLength: 1, maxLength: 8 }), scale, (a, b, pts, s) => {
        const transform = rigidBetween(a, b, s);
        for (const p of pts) {
          expect(dist(world(a, p, s), world(b, applyRigid(transform, p), s))).toBeLessThanOrEqual(
            1e-9,
          );
        }
      }),
    );
  });

  it("round-trips local, world and paper coordinates", () => {
    fc.assert(
      fc.property(frame, point, scale, (f, p, s) => {
        expect(dist(localToWorld(f, s, p), world(f, p, s))).toBeLessThanOrEqual(1e-9);
        expect(dist(worldToLocal(f, s, localToWorld(f, s, p)), p)).toBeLessThanOrEqual(1e-9);
        const paper = localToPaper(f, s, p);
        expect(dist(paperToWorld(paper, s), localToWorld(f, s, p))).toBeLessThanOrEqual(1e-9);
        expect(dist(worldToPaper(paperToWorld(paper, s), s), paper)).toBeLessThanOrEqual(1e-9);
      }),
    );
  });

  it("a frame set from a click and a drag puts the click at the origin and the drag on +X", () => {
    fc.assert(
      fc.property(frame, point, point, scale, (current, click, release, s) => {
        fc.pre(dist(click, release) > 1e-3);
        const next: LocalFrame = {
          origin_paper_mm: localToPaper(current, s, click),
          x_axis_angle_deg: worldAxisAngle(current, directionDeg(click, release)),
          north_angle_deg: 0,
        };
        // What the server does to the entities with the frame the client sends.
        const transform = rigidBetween(current, next, s);
        const origin = applyRigid(transform, click);
        const tip = applyRigid(transform, release);
        expect(dist(origin, [0, 0])).toBeLessThanOrEqual(1e-9);
        expect(Math.abs(tip[1])).toBeLessThanOrEqual(1e-9);
        expect(tip[0]).toBeCloseTo(dist(click, release), 9);
      }),
    );
  });

  it("tells placement changes from North-only changes", () => {
    expect(samePlacement(FIRST, { ...FIRST, north_angle_deg: 40 })).toBe(true);
    expect(samePlacement(FIRST, { ...FIRST, x_axis_angle_deg: 1 })).toBe(false);
    expect(samePlacement(FIRST, { ...FIRST, origin_paper_mm: [12.5, 31] })).toBe(false);
    expect(directionDeg([0, 0], [0, -1])).toBe(270);
  });
});
