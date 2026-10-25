// Polyline tool (jotdown H2, CU-04): each click adds a vertex. Enter or a double-click finishes an
// open polyline (2+ vertices); Shift + click adds the last vertex and closes it, and C closes it
// with the vertices placed so far (3+ vertices either way). Backspace removes the last vertex and
// Esc drops the drawing. The entity goes to the palette's active layer through the editor store.
// Each vertex is snapped like the line tool's (orthogonally to the previous vertex); creation is
// recorded as `create` only.
import type { Point2 } from "../store/types";
import { addOnDrawingLayer, drawingLayer, type EditorTool, type ToolContext } from "../ui/editorUi";
import { DraftPreview, apart, snappedDrawPoint } from "./LineTool";

/** Vertices the API requires (spec 8.2): an open polyline needs 2, a closed one 3. */
export const MIN_OPEN_VERTICES = 2;
export const MIN_CLOSED_VERTICES = 3;

export function createPolylineTool(context: ToolContext): EditorTool {
  const { ui } = context;

  /** Create the polyline if it has enough vertices; returns whether it did. */
  function finish(points: Point2[], closed: boolean): boolean {
    if (points.length < (closed ? MIN_CLOSED_VERTICES : MIN_OPEN_VERTICES)) return false;
    addOnDrawingLayer(context, { kind: "polyline", coords_m: points, closed });
    ui.getState().cancelDraft();
    return true;
  }

  return {
    onPointerDown(pointer) {
      const { draft } = ui.getState();
      const last = draft[draft.length - 1];
      const point = snappedDrawPoint(context, pointer, last === undefined ? [] : [last]);
      if (point === null || drawingLayer(ui.getState(), context.catalog) === null) return true;
      // A press on the previous vertex (the second press of a double-click) adds nothing.
      const next =
        last !== undefined && !apart(last, point, pointer.scale) ? draft : [...draft, point];
      if (!(pointer.shiftKey && finish(next, true))) ui.getState().setDraft(next);
      return true;
    },
    onPointerMove(pointer) {
      const last = ui.getState().draft.at(-1);
      if (last !== undefined) ui.getState().setHover(snappedDrawPoint(context, pointer, [last]));
    },
    onDoubleClick() {
      finish(ui.getState().draft, false);
    },
    onKeyDown(event) {
      const { draft, setDraft, cancelDraft } = ui.getState();
      if (draft.length === 0) return false;
      switch (event.key) {
        case "Enter":
          finish(draft, false);
          return true;
        case "Escape":
          cancelDraft();
          return true;
        case "Backspace":
          setDraft(draft.slice(0, -1));
          return true;
        case "c":
        case "C":
          finish(draft, true);
          return true;
        default:
          return false;
      }
    },
    preview: <DraftPreview ui={ui} />,
  };
}
