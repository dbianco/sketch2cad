import { describe, expect, it } from "vitest";
import type { EntitiesDoc, Point2 } from "../store/types";
import {
  ORTHOGONAL_TOLERANCE_DEG,
  collectSnapCandidates,
  neighbourVertices,
  snapEndpoint,
  snapGrid,
  snapOperation,
  snapOrthogonal,
  snapOrthogonalToAnchors,
  snapPoint,
  withVertex,
  type SnapOptions,
} from "./snap";

/** End point of a segment of `length` from the origin at `deg` degrees from the X axis. */
function polar(deg: number, length = 10, from: Point2 = [0, 0]): Point2 {
  const rad = (deg * Math.PI) / 180;
  return [from[0] + length * Math.cos(rad), from[1] + length * Math.sin(rad)];
}

describe("snapOrthogonal", () => {
  it("uses the spec tolerance of 0.5 degrees", () => {
    expect(ORTHOGONAL_TOLERANCE_DEG).toBe(0.5);
  });

  it("snaps a segment within 0.5° of horizontal to exactly horizontal", () => {
    for (const deg of [0.1, 0.3, 0.49, -0.49, 179.6]) {
      const to = polar(deg);
      const result = snapOrthogonal([0, 0], to);
      expect(result).toEqual({ point: [to[0], 0], snapped: true, kind: "orthogonal" });
    }
  });

  it("does not snap a segment outside the tolerance (0.6°)", () => {
    for (const deg of [0.6, -0.6, 1, 45, 89.4]) {
      const to = polar(deg);
      expect(snapOrthogonal([0, 0], to)).toEqual({ point: to, snapped: false, kind: null });
    }
  });

  it("snaps to exactly vertical within 0.5° of 90°, from any start point", () => {
    const from: Point2 = [3, -2];
    const to = polar(90.4, 5, from);
    expect(snapOrthogonal(from, to)).toEqual({
      point: [3, to[1]],
      snapped: true,
      kind: "orthogonal",
    });
    expect(snapOrthogonal(from, polar(90.6, 5, from)).snapped).toBe(false);
  });

  it("accepts a custom tolerance and ignores a zero-length segment", () => {
    expect(snapOrthogonal([0, 0], polar(2), 3).snapped).toBe(true);
    expect(snapOrthogonal([1, 1], [1, 1]).snapped).toBe(false);
  });

  it("squares a corner against two anchors at once", () => {
    const result = snapOrthogonalToAnchors([10.03, 5.04], [
      [0, 5],
      [10, 0],
    ]);
    expect(result).toEqual({ point: [10, 5], snapped: true, kind: "orthogonal" });
  });
});

describe("snapEndpoint", () => {
  const candidates: Point2[] = [
    [0, 0],
    [1, 0],
    [1.05, 0],
  ];

  it("returns the nearest candidate within the radius", () => {
    expect(snapEndpoint([1.04, 0.01], candidates, 0.1)).toEqual({
      point: [1.05, 0],
      snapped: true,
      kind: "endpoint",
    });
  });

  it("does not snap beyond the radius", () => {
    expect(snapEndpoint([0.5, 0.5], candidates, 0.1).snapped).toBe(false);
    expect(snapEndpoint([0.5, 0.5], [], 10).snapped).toBe(false);
  });
});

describe("snapGrid", () => {
  it("rounds to the nearest grid node without float noise", () => {
    expect(snapGrid([0.31, -0.26], 0.1)).toEqual({
      point: [0.3, -0.3],
      snapped: true,
      kind: "grid",
    });
    expect(snapGrid([1.2, 0.7], 0.5).point).toEqual([1, 0.5]);
    expect(Object.is(snapGrid([-0.01, 0], 0.1).point[0], 0)).toBe(true);
  });

  it("ignores an invalid grid size", () => {
    for (const grid of [0, -1, NaN, Infinity]) {
      expect(snapGrid([0.31, 0.26], grid)).toEqual({
        point: [0.31, 0.26],
        snapped: false,
        kind: null,
      });
    }
  });
});

describe("snapPoint priority", () => {
  const options: SnapOptions = {
    endpoint: true,
    orthogonal: true,
    grid: true,
    candidates: [[10, 0.04]],
    radiusWorld: 0.1,
    anchors: [[0, 0]],
    gridM: 1,
  };

  it("prefers endpoint over orthogonal and grid", () => {
    expect(snapPoint([10.02, 0.05], options)).toMatchObject({ point: [10, 0.04], kind: "endpoint" });
  });

  it("prefers orthogonal over grid when no endpoint is close", () => {
    expect(snapPoint([7.3, 0.03], options)).toMatchObject({ point: [7.3, 0], kind: "orthogonal" });
  });

  it("falls back to grid, and to the raw point with every snap off", () => {
    expect(snapPoint([7.3, 3.2], options)).toMatchObject({ point: [7, 3], kind: "grid" });
    const off = { ...options, endpoint: false, orthogonal: false, grid: false };
    expect(snapPoint([10.02, 0.05], off)).toEqual({
      point: [10.02, 0.05],
      snapped: false,
      kind: null,
    });
  });

  it("names the PATCH operation of each snap", () => {
    expect(snapOperation("endpoint")).toBe("snap_endpoint");
    expect(snapOperation("orthogonal")).toBe("snap_orthogonal");
    expect(snapOperation("grid")).toBe("snap_grid");
  });
});

describe("geometry helpers", () => {
  it("finds the neighbours of a vertex, wrapping closed polylines", () => {
    const open = {
      kind: "polyline" as const,
      closed: false,
      coords_m: [
        [0, 0],
        [1, 0],
        [1, 1],
      ] as Point2[],
    };
    expect(neighbourVertices(open, 0)).toEqual([[1, 0]]);
    expect(neighbourVertices(open, 1)).toEqual([
      [0, 0],
      [1, 1],
    ]);
    expect(neighbourVertices({ ...open, closed: true }, 0)).toEqual([
      [1, 1],
      [1, 0],
    ]);
    expect(neighbourVertices({ kind: "point", coords_m: [[0, 0]] }, 0)).toEqual([]);
  });

  it("replaces one vertex without touching the input and rejects unknown indices", () => {
    const line = {
      kind: "line" as const,
      coords_m: [
        [0, 0],
        [1, 0],
      ] as Point2[],
    };
    expect(withVertex(line, 1, [2, 0])).toEqual({
      kind: "line",
      coords_m: [
        [0, 0],
        [2, 0],
      ],
    });
    expect(line.coords_m[1]).toEqual([1, 0]);
    expect(() => withVertex(line, 2, [0, 0])).toThrow(RangeError);
  });

  it("collects vertices of other live entities on accepted layers", () => {
    const base = { attributes: {}, deleted: false, remote: null };
    const doc: EntitiesDoc = {
      order: ["a", "b", "c", "d"],
      byId: {
        a: { ...base, id: "a", type: "point", layer: "L1", geometry: { kind: "point", coords_m: [[1, 1]] } },
        b: { ...base, id: "b", type: "point", layer: "L1", geometry: { kind: "point", coords_m: [[2, 2]] } },
        c: { ...base, id: "c", type: "point", layer: "L2", geometry: { kind: "point", coords_m: [[3, 3]] } },
        d: {
          ...base,
          deleted: true,
          id: "d",
          type: "point",
          layer: "L1",
          geometry: { kind: "point", coords_m: [[4, 4]] },
        },
      },
    };
    expect(collectSnapCandidates(doc, "a", (layer) => layer === "L1")).toEqual([[2, 2]]);
  });
});
