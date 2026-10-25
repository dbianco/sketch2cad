// Editor workspace (jotdown H2): palette, canvas with the active tool, and the selection panel.
// The UI store (ui/editorUi.ts) holds the active tool and layer and the layer flags, which the
// canvas receives as controlled props so that tools respect locked and hidden layers.
//
// Pointer: with the Select tool a press on an entity selects it and a press on the background
// pans; drawing tools take every primary-button press, so with them the canvas pans with the
// middle button or Space + drag. Keys: the palette's shortcuts (V, L, P, O, T) and the active
// tool's keys (Esc, Enter, Backspace, Delete, C) work anywhere except while typing in a field.
import { useEffect, useMemo } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { EditorCanvas, type EditorCanvasProps } from "./canvas/EditorCanvas";
import { Palette, isTypingTarget } from "./panels/Palette";
import { editorStore, type EditorStore } from "./store/entities";
import { createLineTool } from "./tools/LineTool";
import { createPointTool, createTextTool } from "./tools/PointTextTools";
import { createPolylineTool } from "./tools/PolylineTool";
import { SelectionPanel, createSelectTool } from "./tools/SelectTool";
import {
  editorUi,
  type EditorTool,
  type EditorUiStore,
  type ToolContext,
  type ToolId,
} from "./ui/editorUi";

export const TOOL_FACTORIES: Readonly<Record<ToolId, (context: ToolContext) => EditorTool>> = {
  select: createSelectTool,
  line: createLineTool,
  polyline: createPolylineTool,
  point: createPointTool,
  text: createTextTool,
};

export type EditorWorkspaceProps = Omit<
  EditorCanvasProps,
  "store" | "tool" | "layerFlags" | "onLayerFlagsChange"
> & {
  store?: StoreApi<EditorStore>;
  ui?: StoreApi<EditorUiStore>;
};

export function EditorWorkspace({
  store = editorStore,
  ui = editorUi,
  catalog,
  ...canvasProps
}: EditorWorkspaceProps) {
  const activeTool = useStore(ui, (state) => state.activeTool);
  const layerFlags = useStore(ui, (state) => state.layerFlags);
  const tool = useMemo(
    () => TOOL_FACTORIES[activeTool]({ store, ui, catalog }),
    [activeTool, store, ui, catalog],
  );

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey || isTypingTarget(event.target)) return;
      if (tool.onKeyDown?.(event) === true) event.preventDefault();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [tool]);

  return (
    <div className="editor-workspace" style={{ display: "flex" }}>
      <Palette ui={ui} catalog={catalog} />
      <EditorCanvas
        {...canvasProps}
        catalog={catalog}
        store={store}
        tool={tool}
        layerFlags={layerFlags}
        onLayerFlagsChange={(flags) => ui.getState().setLayerFlags(flags)}
      />
      {activeTool === "select" && <SelectionPanel store={store} ui={ui} catalog={catalog} />}
    </div>
  );
}
