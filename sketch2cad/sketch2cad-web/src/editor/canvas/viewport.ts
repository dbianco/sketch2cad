// Camera of the editor canvas (decision D-14): pan and zoom only change the `<svg>` viewBox.
//
// World coordinates are metres with Y up (the store's `coords_m`). SVG user units are metres
// too, but with Y down: an entity at world (x, y) is drawn at SVG (x, -y). Screen coordinates
// are CSS pixels from the top-left corner of the `<svg>` element, Y down.
import type { Point2 } from "../store/types";

/** Camera: world point at the centre of the canvas, zoom and canvas size. */
export interface Viewport {
  /** World X (m) shown at the centre of the canvas. */
  centerX: number;
  /** World Y (m, Y up) shown at the centre of the canvas. */
  centerY: number;
  /** Zoom, in screen pixels per world metre. */
  scale: number;
  /** Canvas width in CSS pixels. */
  width: number;
  /** Canvas height in CSS pixels. */
  height: number;
}

/** Axis-aligned rectangle in world metres (Y up). */
export interface WorldRect {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** A `viewBox` in SVG user units (metres, Y down). */
export interface ViewBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Zoom limits in px per metre. 0.05 px/m shows 20 km across 1000 px, more than any plan;
 * 2000 px/m shows half a millimetre per pixel, finer than a hand sketch can be measured. The
 * upper bound also keeps the screen/world round trip exact within 1e-9 px for plans of a few
 * kilometres around the origin.
 */
export const MIN_SCALE = 0.05;
export const MAX_SCALE = 2000;

/** Margin, in screen pixels, that `fitToBounds` leaves around the fitted rectangle. */
export const FIT_PADDING_PX = 24;

/** Size of the world shown by `fitToBounds` around a degenerate (point or empty) rectangle. */
export const MIN_FIT_SPAN_M = 1;

export function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

export function worldToScreen(viewport: Viewport, point: Point2): Point2 {
  const { centerX, centerY, scale, width, height } = viewport;
  return [(point[0] - centerX) * scale + width / 2, (centerY - point[1]) * scale + height / 2];
}

export function screenToWorld(viewport: Viewport, point: Point2): Point2 {
  const { centerX, centerY, scale, width, height } = viewport;
  return [centerX + (point[0] - width / 2) / scale, centerY - (point[1] - height / 2) / scale];
}

/** The `viewBox` that shows the viewport: SVG units are metres with Y flipped. */
export function viewBoxOf(viewport: Viewport): ViewBox {
  const { centerX, centerY, scale, width, height } = viewport;
  const spanX = width / scale;
  const spanY = height / scale;
  return { x: centerX - spanX / 2, y: -centerY - spanY / 2, width: spanX, height: spanY };
}

export function viewBoxAttribute(viewport: Viewport): string {
  const { x, y, width, height } = viewBoxOf(viewport);
  return `${String(x)} ${String(y)} ${String(width)} ${String(height)}`;
}

/** Multiply the zoom by `factor` keeping the world point under `screenPoint` fixed. */
export function zoomAt(viewport: Viewport, screenPoint: Point2, factor: number): Viewport {
  const scale = clampScale(viewport.scale * factor);
  const [wx, wy] = screenToWorld(viewport, screenPoint);
  const dx = screenPoint[0] - viewport.width / 2;
  const dy = screenPoint[1] - viewport.height / 2;
  return { ...viewport, scale, centerX: wx - dx / scale, centerY: wy + dy / scale };
}

/** Move the content by (dx, dy) screen pixels, as when dragging it. */
export function pan(viewport: Viewport, dx: number, dy: number): Viewport {
  return {
    ...viewport,
    centerX: viewport.centerX - dx / viewport.scale,
    centerY: viewport.centerY + dy / viewport.scale,
  };
}

/** Change the canvas size keeping the centre and the zoom. */
export function resize(viewport: Viewport, width: number, height: number): Viewport {
  return { ...viewport, width, height };
}

/** Centre `bounds` and zoom so that it fits in the canvas with `padding` px around it. */
export function fitToBounds(
  bounds: WorldRect,
  width: number,
  height: number,
  padding: number = FIT_PADDING_PX,
): Viewport {
  const spanX = Math.max(bounds.maxX - bounds.minX, MIN_FIT_SPAN_M);
  const spanY = Math.max(bounds.maxY - bounds.minY, MIN_FIT_SPAN_M);
  const usableX = Math.max(width - 2 * padding, 1);
  const usableY = Math.max(height - 2 * padding, 1);
  return {
    centerX: (bounds.minX + bounds.maxX) / 2,
    centerY: (bounds.minY + bounds.maxY) / 2,
    scale: clampScale(Math.min(usableX / spanX, usableY / spanY)),
    width,
    height,
  };
}

/** Bounding rectangle of world points; `null` when there are none. */
export function boundsOf(points: Iterable<Point2>): WorldRect | null {
  let rect: WorldRect | null = null;
  for (const [x, y] of points) {
    if (rect === null) {
      rect = { minX: x, minY: y, maxX: x, maxY: y };
    } else {
      rect.minX = Math.min(rect.minX, x);
      rect.minY = Math.min(rect.minY, y);
      rect.maxX = Math.max(rect.maxX, x);
      rect.maxY = Math.max(rect.maxY, y);
    }
  }
  return rect;
}
