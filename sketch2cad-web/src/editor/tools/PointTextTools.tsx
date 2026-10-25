// Point and text tools (jotdown H2, CU-04). Point: a click creates a `point` entity. Text: a click
// places the anchor and opens an inline input; Enter creates a `text` entity with the typed
// content (blank content creates nothing) and Esc drops it. Both go to the palette's active
// layer through the editor store.
import { useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { worldToScreen, type Viewport } from "../canvas/viewport";
import type { Point2 } from "../store/types";
import {
  addOnDrawingLayer,
  drawingLayer,
  worldPoint,
  type EditorTool,
  type ToolContext,
} from "../ui/editorUi";

/**
 * Paper height of new texts, in mm: 2.5 mm, the usual lettering height of technical drawings
 * (ISO 3098) and the height of the canvas's dimension text. At 1:100 it is 0.25 m.
 */
export const TEXT_HEIGHT_MM_PAPER = 2.5;

export const TEXT_TOOL_TEXT = {
  input: "Contenido del texto",
  hint: "Intro para crear, Esc para cancelar",
} as const;

export function createPointTool(context: ToolContext): EditorTool {
  return {
    onPointerDown(pointer) {
      const point = worldPoint(pointer.world);
      if (point !== null) addOnDrawingLayer(context, { kind: "point", coords_m: [point] });
      return true;
    },
  };
}

function TextInput({
  context,
  anchor,
  viewport,
}: {
  context: ToolContext;
  anchor: Point2;
  viewport: Viewport;
}) {
  const [content, setContent] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
    // The press that placed the anchor focuses the canvas after this mounts; take focus back.
    const timer = setTimeout(() => inputRef.current?.focus());
    return () => clearTimeout(timer);
  }, []);
  const [left, top] = worldToScreen(viewport, anchor);
  return (
    <div className="editor-text-input" style={{ position: "absolute", left, top }}>
      <input
        ref={inputRef}
        type="text"
        aria-label={TEXT_TOOL_TEXT.input}
        title={TEXT_TOOL_TEXT.hint}
        value={content}
        onChange={(event) => setContent(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            context.ui.getState().cancelDraft();
          } else if (event.key === "Enter" && content.trim() !== "") {
            addOnDrawingLayer(context, {
              kind: "text",
              anchor_m: anchor,
              content: content.trim(),
              height_mm_paper: TEXT_HEIGHT_MM_PAPER,
              rotation_deg: 0,
            });
            context.ui.getState().cancelDraft();
          }
        }}
      />
    </div>
  );
}

function TextOverlay({ context, viewport }: { context: ToolContext; viewport: Viewport }) {
  const anchor = useStore(context.ui, (state) => state.draft[0]);
  if (anchor === undefined) return null;
  return (
    <TextInput
      key={`${String(anchor[0])},${String(anchor[1])}`}
      context={context}
      anchor={anchor}
      viewport={viewport}
    />
  );
}

export function createTextTool(context: ToolContext): EditorTool {
  const { ui } = context;
  return {
    onPointerDown(pointer) {
      const point = worldPoint(pointer.world);
      if (point !== null && drawingLayer(ui.getState(), context.catalog) !== null) {
        ui.getState().setDraft([point]);
      }
      return true;
    },
    onKeyDown(event) {
      if (event.key !== "Escape" || ui.getState().draft.length === 0) return false;
      ui.getState().cancelDraft();
      return true;
    },
    renderOverlay: (viewport) => <TextOverlay context={context} viewport={viewport} />,
  };
}
