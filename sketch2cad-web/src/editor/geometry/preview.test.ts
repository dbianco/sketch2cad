import { describe, expect, it } from "vitest";
import type { Point2 } from "../store/types";
import { baselineOffsetPoint, circleIntersections, triangulate } from "./preview";
import { BASELINE_FIXTURES, TRIANGULATION_FIXTURES } from "./serverFixtures";

/** Agreement with the server's expected points, in metres. */
const AGREEMENT_M = 1e-6;

function expectClose(actual: Point2 | null, expected: Point2): void {
  expect(actual).not.toBeNull();
  const [x, y] = actual as Point2;
  expect(Math.hypot(x - expected[0], y - expected[1])).toBeLessThanOrEqual(AGREEMENT_M);
}

describe("preview geometry agrees with the server fixtures", () => {
  it("has five server fixtures", () => {
    expect(TRIANGULATION_FIXTURES.length + BASELINE_FIXTURES.length).toBe(5);
  });

  it.each(TRIANGULATION_FIXTURES)("triangulation $id", (c) => {
    expectClose(triangulate(c.a, c.b, c.dA, c.dB, c.solution, c.hint).point, c.expected);
  });

  it.each(BASELINE_FIXTURES)("baseline offset $id", (c) => {
    expectClose(baselineOffsetPoint(c.a, c.b, c.s, c.d, c.side), c.expected);
  });
});

describe("triangulation candidates", () => {
  it("lists the left candidate of A→B first and marks the nearest to the hint", () => {
    const below = triangulate([0, 0], [5, 0], 3, 4, "nearest", [2, -2]);
    expectClose(below.candidates[0] ?? null, [1.8, 2.4]);
    expectClose(below.candidates[1] ?? null, [1.8, -2.4]);
    expect(below.nearestIndex).toBe(1);
  });

  it("breaks a tie, or no hint, towards the left candidate", () => {
    expect(triangulate([0, 0], [5, 0], 3, 4, "nearest", [1.8, 0]).nearestIndex).toBe(0);
    expect(triangulate([0, 0], [5, 0], 3, 4, "alt").point).toEqual(
      triangulate([0, 0], [5, 0], 3, 4, "nearest", [9, -9]).point,
    );
  });

  // test_triangulation_matches_hand_solution[tangent 2+3=5]
  it("gives one candidate for tangent circles, whatever the solution", () => {
    const tangent = triangulate([0, 0], [5, 0], 2, 3, "alt", [0, 9]);
    expect(tangent.candidates).toHaveLength(1);
    expectClose(tangent.point, [2, 0]);
  });

  it("accepts a gap within the 1e-9 m tolerance as tangent and rejects a larger one", () => {
    expect(circleIntersections([0, 0], 2, [5 + 5e-10, 0], 3)).toHaveLength(1);
    expect(circleIntersections([0, 0], 2, [5 + 1e-6, 0], 3)).toEqual([]);
  });

  it("has no candidates for disjoint, contained or concentric circles (V-06)", () => {
    expect(triangulate([0, 0], [10, 0], 2, 3).candidates).toEqual([]);
    expect(triangulate([0, 0], [1, 0], 5, 1).point).toBeNull();
    expect(triangulate([0, 0], [0, 0], 2, 2).nearestIndex).toBeNull();
  });
});

describe("baseline offset", () => {
  it("returns null for coincident base points", () => {
    expect(baselineOffsetPoint([1, 1], [1, 1], 2, 1, "left")).toBeNull();
  });

  // test_baseline_offset_matches_hand_solution[south negative s]
  it("goes behind A for a negative s", () => {
    expectClose(baselineOffsetPoint([0, 0], [0, -4], -2, 3, "left"), [3, 2]);
  });
});
