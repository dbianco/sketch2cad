import fc from "fast-check";
import { describe, expect, it } from "vitest";
import type { Point2 } from "../store/types";
import {
  MAX_SCALE,
  MIN_SCALE,
  fitToBounds,
  pan,
  screenToWorld,
  viewBoxOf,
  worldToScreen,
  zoomAt,
  type Viewport,
} from "./viewport";

const VIEWPORT: Viewport = { centerX: 10, centerY: 5, scale: 20, width: 800, height: 600 };

const viewportArb: fc.Arbitrary<Viewport> = fc.record({
  centerX: fc.double({ min: -1000, max: 1000, noNaN: true }),
  centerY: fc.double({ min: -1000, max: 1000, noNaN: true }),
  scale: fc.double({ min: MIN_SCALE, max: MAX_SCALE, noNaN: true }),
  width: fc.integer({ min: 1, max: 4000 }),
  height: fc.integer({ min: 1, max: 4000 }),
});

const screenPointArb: fc.Arbitrary<Point2> = fc.tuple(
  fc.double({ min: -5000, max: 5000, noNaN: true }),
  fc.double({ min: -5000, max: 5000, noNaN: true }),
);

function expectClose(actual: Point2, expected: Point2, tolerance: number): void {
  expect(Math.abs(actual[0] - expected[0])).toBeLessThanOrEqual(tolerance);
  expect(Math.abs(actual[1] - expected[1])).toBeLessThanOrEqual(tolerance);
}

describe("screen and world conversion", () => {
  it("round-trips worldToScreen(screenToWorld(p)) within 1e-9 for 1000 random points", () => {
    fc.assert(
      fc.property(viewportArb, screenPointArb, (viewport, point) => {
        expectClose(worldToScreen(viewport, screenToWorld(viewport, point)), point, 1e-9);
      }),
      { numRuns: 1000 },
    );
  });

  it("maps the centre to the middle of the canvas and inverts the Y axis", () => {
    expect(worldToScreen(VIEWPORT, [10, 5])).toEqual([400, 300]);
    // One metre up in the world is 20 px up (smaller y) on screen.
    expect(worldToScreen(VIEWPORT, [11, 6])).toEqual([420, 280]);
  });
});

describe("viewBox", () => {
  it("frames the viewport in metres with Y flipped", () => {
    expect(viewBoxOf(VIEWPORT)).toEqual({ x: -10, y: -20, width: 40, height: 30 });
  });

  it("puts every world point at the screen position the viewport gives it", () => {
    fc.assert(
      fc.property(viewportArb, screenPointArb, (viewport, point) => {
        const box = viewBoxOf(viewport);
        const [wx, wy] = screenToWorld(viewport, point);
        // SVG user point (wx, -wy) seen through the viewBox, as a browser maps it.
        const sx = ((wx - box.x) / box.width) * viewport.width;
        const sy = ((-wy - box.y) / box.height) * viewport.height;
        expectClose([sx, sy], point, 1e-6);
      }),
    );
  });
});

describe("camera operations", () => {
  it("zoomAt keeps the world point under the cursor fixed", () => {
    fc.assert(
      fc.property(
        viewportArb,
        screenPointArb,
        fc.double({ min: 0.1, max: 10, noNaN: true }),
        (viewport, point, factor) => {
          const before = screenToWorld(viewport, point);
          const after = screenToWorld(zoomAt(viewport, point, factor), point);
          expectClose(after, before, 1e-6);
        },
      ),
    );
  });

  it("zoomAt clamps the scale to the documented limits", () => {
    expect(zoomAt(VIEWPORT, [0, 0], 1e9).scale).toBe(MAX_SCALE);
    expect(zoomAt(VIEWPORT, [0, 0], 1e-9).scale).toBe(MIN_SCALE);
  });

  it("pan moves the content with the pointer", () => {
    const moved = pan(VIEWPORT, 40, -20);
    expect(worldToScreen(moved, [10, 5])).toEqual([440, 280]);
    expect(moved.scale).toBe(VIEWPORT.scale);
  });

  it("fitToBounds centres the rectangle and fits its larger side", () => {
    const fitted = fitToBounds({ minX: 0, minY: 0, maxX: 60, maxY: 40 }, 1000, 500, 0);
    expect(fitted.centerX).toBe(30);
    expect(fitted.centerY).toBe(20);
    expect(fitted.scale).toBe(12.5);
  });

  it("fitToBounds of a single point shows a minimum span instead of dividing by zero", () => {
    const fitted = fitToBounds({ minX: 3, minY: 4, maxX: 3, maxY: 4 }, 100, 100, 0);
    expect(fitted.scale).toBe(100);
    expect([fitted.centerX, fitted.centerY]).toEqual([3, 4]);
  });
});
