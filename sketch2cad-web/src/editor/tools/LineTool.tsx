// Line tool (jotdown H2, CU-04): two clicks, or a press-drag-release, create a `line` entity on
// the palette's active layer through the editor store. Esc drops the first point. Both points are
// snapped with the snap settings (the end also orthogonally to the start); a new entity is
// recorded only as `create` with the snapped coordinates: snap operation names are for edits.
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { svgPoints, type CanvasPointer } from "../canvas/EditorCanvas";
import { resolveSnap } from "../panels/SnapSettings";
import type { Point2 } from "../store/types";
import {
  addOnDrawingLayer,
  drawingLayer,
  worldPoint,
  type EditorTool,
  type EditorUiStore,
  type ToolContext,
} from "../ui/editorUi";

/**
 * Screen distance, in px, under which two pointer positions count as the same place: a press
 * and release closer than this is a click, not a drag, and a vertex closer than this to the
 * previous one is a repeated click (e.g. the second press of a double-click).
 */
export const SAME_PLACE_PX = 3;

/** Whether `a` and `b` are at least SAME_PLACE_PX apart on screen. */
export function apart(a: Point2, b: Point2, scale: number): boolean {
  return Math.hypot(a[0] - b[0], a[1] - b[1]) * scale >= SAME_PLACE_PX;
}

/** Pointer world point snapped for a new vertex joined to `anchors`; null when not finite. */
export function snappedDrawPoint(
  context: ToolContext,
  pointer: CanvasPointer,
  anchors: readonly Point2[],
): Point2 | null {
  const point = worldPoint(pointer.world);
  if (point === null) return null;
  return resolveSnap(context, point, { anchors, exclude: null, scale: pointer.scale }).point;
}

/** Rubber band from the draft points to the pointer. */
export function DraftPreview({ ui }: { ui: StoreApi<EditorUiStore> }) {
  const draft = useStore(ui, (state) => state.draft);
  const hover = useStore(ui, (state) => state.hover);
  if (draft.length === 0) return null;
  return (
    <polyline
      data-testid="draft-preview"
      points={svgPoints(hover === null ? draft : [...draft, hover])}
      fill="none"
      stroke="currentColor"
      strokeDasharray="4 4"
      strokeWidth={1}
      vectorEffect="non-scaling-stroke"
    />
  );
}

export function createLineTool(context: ToolContext): EditorTool {
  const { ui } = context;
  /** True while the press that placed the first point is held: its release may end a drag. */
  let dragging = false;

  function finish(start: Point2, end: Point2, scale: number): void {
    if (!apart(start, end, scale)) return;
    addOnDrawingLayer(context, { kind: "line", coords_m: [start, end] });
    ui.getState().cancelDraft();
  }

  return {
    onPointerDown(pointer: CanvasPointer) {
      const [start] = ui.getState().draft;
      const point = snappedDrawPoint(context, pointer, start === undefined ? [] : [start]);
      if (point === null || drawingLayer(ui.getState(), context.catalog) === null) return true;
      if (start === undefined) {
        ui.getState().setDraft([point]);
        dragging = true;
      } else {
        finish(start, point, pointer.scale);
      }
      return true;
    },
    onPointerMove(pointer) {
      const [start] = ui.getState().draft;
      if (start !== undefined) ui.getState().setHover(snappedDrawPoint(context, pointer, [start]));
    },
    onPointerUp(pointer) {
      const [start] = ui.getState().draft;
      const point = start === undefined ? null : snappedDrawPoint(context, pointer, [start]);
      if (dragging && start !== undefined && point !== null && apart(start, point, pointer.scale)) {
        finish(start, point, pointer.scale);
      }
      dragging = false;
    },
    onKeyDown(event) {
      if (event.key !== "Escape" || ui.getState().draft.length === 0) return false;
      ui.getState().cancelDraft();
      return true;
    },
    preview: <DraftPreview ui={ui} />,
  };
}
