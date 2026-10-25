// Snapping (spec 7, CU-04 "snapping registrado"; jotdown H2): pure functions that move a point
// to an endpoint of another entity, to an exactly horizontal/vertical segment, or to the grid.
//
// Coordinates are world metres (`coords_m`), which are already expressed in the plan's local
// frame (`local_frame` sets origin and X axis when the plan is calibrated). "Orthogonal relative
// to the local axes" is therefore orthogonal to the x/y axes of `coords_m`, and the grid is
// aligned with those axes with a node at the local origin.
//
// When several snaps apply, `snapPoint` keeps one by priority endpoint > orthogonal > grid and
// reports its kind, so the edit is recorded with `snap_endpoint`, `snap_orthogonal` or
// `snap_grid` (spec 9.1 PATCH /entities/{id}).
import type { EntitiesDoc, EntityId, Geometry, Point2, SnapOperation } from "../store/types";

/** Spec 7: a segment within 0.5° of 0° or 90° (local axes) becomes exactly horizontal/vertical. */
export const ORTHOGONAL_TOLERANCE_DEG = 0.5;

/**
 * Screen radius, in px, within which a vertex of another entity captures the pointer. Converted
 * to metres with the viewport scale so that capture feels the same at any zoom.
 */
export const ENDPOINT_SNAP_RADIUS_PX = 8;

/** Decimals kept by grid snapping (nanometres): avoids float noise such as 0.30000000000000004. */
export const GRID_DECIMALS = 9;

export type SnapKind = "endpoint" | "orthogonal" | "grid";

export interface SnapResult {
  point: Point2;
  /** Whether the snap rule applied (the point is the snap's, not the raw input). */
  snapped: boolean;
  kind: SnapKind | null;
}

function none(point: Point2): SnapResult {
  return { point: [point[0], point[1]], snapped: false, kind: null };
}

/** Deviation, in degrees within [0, 90], of the segment from..to from the local X axis. */
function angleFromX(from: Point2, to: Point2): number | null {
  const dx = Math.abs(to[0] - from[0]);
  const dy = Math.abs(to[1] - from[1]);
  if (dx === 0 && dy === 0) return null;
  return (Math.atan2(dy, dx) * 180) / Math.PI;
}

/**
 * Make `from`..`to` exactly horizontal or vertical when it is within `toleranceDeg` of 0° or 90°.
 * `to` is projected onto the axis through `from` (it keeps its x for horizontal, its y for
 * vertical), as CAD ortho modes do. The tolerance is inclusive.
 */
export function snapOrthogonal(
  from: Point2,
  to: Point2,
  toleranceDeg: number = ORTHOGONAL_TOLERANCE_DEG,
): SnapResult {
  const angle = angleFromX(from, to);
  if (angle === null) return none(to);
  if (angle <= toleranceDeg) return { point: [to[0], from[1]], snapped: true, kind: "orthogonal" };
  if (90 - angle <= toleranceDeg) {
    return { point: [from[0], to[1]], snapped: true, kind: "orthogonal" };
  }
  return none(to);
}

/**
 * Orthogonal snap of `to` against every anchor (the neighbouring vertices of a moved vertex):
 * the closest horizontal alignment fixes y and the closest vertical one fixes x, so a corner
 * between two segments can square both of them at once.
 */
export function snapOrthogonalToAnchors(
  to: Point2,
  anchors: readonly Point2[],
  toleranceDeg: number = ORTHOGONAL_TOLERANCE_DEG,
): SnapResult {
  let horizontal: { y: number; deviation: number } | null = null;
  let vertical: { x: number; deviation: number } | null = null;
  for (const anchor of anchors) {
    const angle = angleFromX(anchor, to);
    if (angle === null) continue;
    if (angle <= toleranceDeg && (horizontal === null || angle < horizontal.deviation)) {
      horizontal = { y: anchor[1], deviation: angle };
    } else if (90 - angle <= toleranceDeg && (vertical === null || 90 - angle < vertical.deviation)) {
      vertical = { x: anchor[0], deviation: 90 - angle };
    }
  }
  if (horizontal === null && vertical === null) return none(to);
  return {
    point: [vertical?.x ?? to[0], horizontal?.y ?? to[1]],
    snapped: true,
    kind: "orthogonal",
  };
}

/** Nearest candidate within `radiusWorld` metres of `point` (inclusive), if any. */
export function snapEndpoint(
  point: Point2,
  candidates: readonly Point2[],
  radiusWorld: number,
): SnapResult {
  let best: Point2 | null = null;
  let bestDistance = Infinity;
  for (const candidate of candidates) {
    const distance = Math.hypot(candidate[0] - point[0], candidate[1] - point[1]);
    if (distance <= radiusWorld && distance < bestDistance) {
      best = candidate;
      bestDistance = distance;
    }
  }
  return best === null ? none(point) : { point: [best[0], best[1]], snapped: true, kind: "endpoint" };
}

/** Whether `gridM` is a usable grid size: finite and greater than 0. */
export function isValidGridSize(gridM: number): boolean {
  return Number.isFinite(gridM) && gridM > 0;
}

function roundToGrid(value: number, gridM: number): number {
  const rounded = Number((Math.round(value / gridM) * gridM).toFixed(GRID_DECIMALS));
  return rounded === 0 ? 0 : rounded;
}

/** Nearest node of a square grid of `gridM` metres with a node at the local origin. */
export function snapGrid(point: Point2, gridM: number): SnapResult {
  if (!isValidGridSize(gridM)) return none(point);
  return {
    point: [roundToGrid(point[0], gridM), roundToGrid(point[1], gridM)],
    snapped: true,
    kind: "grid",
  };
}

export interface SnapOptions {
  endpoint: boolean;
  orthogonal: boolean;
  grid: boolean;
  /** Vertices of other entities for endpoint snapping. */
  candidates: readonly Point2[];
  /** Endpoint capture radius in metres (ENDPOINT_SNAP_RADIUS_PX / viewport scale). */
  radiusWorld: number;
  /** Neighbouring vertices the moved or new vertex forms segments with. */
  anchors: readonly Point2[];
  gridM: number;
  toleranceDeg?: number;
}

/** Apply the enabled snaps with priority endpoint > orthogonal > grid; reports the one used. */
export function snapPoint(point: Point2, options: SnapOptions): SnapResult {
  if (options.endpoint) {
    const result = snapEndpoint(point, options.candidates, options.radiusWorld);
    if (result.snapped) return result;
  }
  if (options.orthogonal) {
    const result = snapOrthogonalToAnchors(point, options.anchors, options.toleranceDeg);
    if (result.snapped) return result;
  }
  if (options.grid) {
    const result = snapGrid(point, options.gridM);
    if (result.snapped) return result;
  }
  return none(point);
}

/** Operation name of the PATCH that records a snap (spec 9.1). */
export function snapOperation(kind: SnapKind): SnapOperation {
  return `snap_${kind}`;
}

// Geometry helpers. Vertex indices follow `EditorStore.moveVertex`: `coords_m[i]`, the anchor
// (0) of a text or symbol, `p1_m` (0) and `p2_m` (1) of a dimension.

/** Editable vertices of a geometry, by moveVertex index. */
export function geometryVertices(geometry: Geometry): Point2[] {
  switch (geometry.kind) {
    case "line":
    case "polyline":
    case "point":
      return geometry.coords_m.map((point) => [point[0], point[1]]);
    case "text":
    case "symbol":
      return [[geometry.anchor_m[0], geometry.anchor_m[1]]];
    case "dimension":
      return [
        [geometry.p1_m[0], geometry.p1_m[1]],
        [geometry.p2_m[0], geometry.p2_m[1]],
      ];
  }
}

/** Vertices joined to vertex `index` by a segment (orthogonal snapping anchors). */
export function neighbourVertices(geometry: Geometry, index: number): Point2[] {
  const vertices = geometryVertices(geometry);
  if (geometry.kind === "point" || vertices.length < 2) return [];
  const last = vertices.length - 1;
  const closed = geometry.kind === "polyline" && geometry.closed;
  const indices = new Set<number>();
  if (index > 0) indices.add(index - 1);
  else if (closed) indices.add(last);
  if (index < last) indices.add(index + 1);
  else if (closed) indices.add(0);
  indices.delete(index);
  return [...indices].map((i) => vertices[i] as Point2);
}

/** Copy of `geometry` with vertex `index` at `point`; throws on an unknown index. */
export function withVertex(geometry: Geometry, index: number, point: Point2): Geometry {
  const value: Point2 = [point[0], point[1]];
  const count = geometryVertices(geometry).length;
  if (!Number.isInteger(index) || index < 0 || index >= count) {
    throw new RangeError(`Vertex ${String(index)} does not exist in a ${geometry.kind} geometry`);
  }
  const copy = structuredClone(geometry);
  switch (copy.kind) {
    case "line":
    case "polyline":
    case "point":
      copy.coords_m[index] = value;
      break;
    case "text":
    case "symbol":
      copy.anchor_m = value;
      break;
    case "dimension":
      if (index === 0) copy.p1_m = value;
      else copy.p2_m = value;
      break;
  }
  return copy;
}

/** Vertices of the live entities other than `exclude` on layers that `includeLayer` accepts. */
export function collectSnapCandidates(
  entities: EntitiesDoc,
  exclude: EntityId | null,
  includeLayer: (layer: string) => boolean = () => true,
): Point2[] {
  const candidates: Point2[] = [];
  for (const id of entities.order) {
    const entity = entities.byId[id];
    if (entity === undefined || entity.deleted || id === exclude) continue;
    if (!includeLayer(entity.layer)) continue;
    candidates.push(...geometryVertices(entity.geometry));
  }
  return candidates;
}
