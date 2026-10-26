// Editor workspace (jotdown H2): palette, canvas with the active tool, and the selection panel.
// The UI store (ui/editorUi.ts) holds the active tool and layer and the layer flags, which the
// canvas receives as controlled props so that tools respect locked and hidden layers.
//
// Pointer: with the Select tool a press on an entity selects it and a press on the background
// pans; drawing tools take every primary-button press, so with them the canvas pans with the
// middle button or Space + drag. Keys: the palette's shortcuts (V, E, L, P, O, T, S, F, D, G, B)
// and the active tool's keys (Esc, Enter, Backspace, Delete, C, arrows on a vertex handle) work
// anywhere except while typing in a field. A tool's `panel` (triangulation, base + offset) is shown
// next to the canvas. The snap settings panel configures snapping for every tool.
// The plan store (plan/planStore.ts) gives the local frame: the north arrow is drawn over the
// canvas once a plan is open, and the local frame tool's panel changes the frame. When the plan
// store says `readOnly` (status or lease, see features/plans/LeaseBanner.tsx) the palette is
// disabled and the canvas gets no tool, so it only selects and pans as a viewer.
// Keyboard (a11y/): with a drawing tool (CURSOR_TOOLS) the focused canvas has a keyboard cursor
// that moves by the snap grid size, and "Ayuda de teclado" (or ?) lists every shortcut.
// "Deshacer" / "Rehacer" (Ctrl/Cmd + Z, Shift + Ctrl/Cmd + Z) run the editor store's history.
import { useEffect, useMemo } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import type { ApiClient } from "../api/client";
import { KeyboardHelp } from "./a11y/KeyboardHelp";
import { CURSOR_TOOLS } from "./a11y/shortcuts";
import { EditorCanvas, type EditorCanvasProps } from "./canvas/EditorCanvas";
import { NorthArrow } from "./canvas/NorthArrow";
import { HistoryButtons } from "./panels/HistoryButtons";
import { Palette, isTypingTarget } from "./panels/Palette";
import { SnapSettings, snapSettings, type SnapSettingsStore } from "./panels/SnapSettings";
import { SymbolPicker, symbolPicker, type SymbolPickerStore } from "./panels/SymbolPicker";
import { planStore, type PlanStore } from "./plan/planStore";
import { editorStore, type EditorStore } from "./store/entities";
import { createBaselineOffsetTool } from "./tools/BaselineOffsetDialog";
import { createFixLengthTool } from "./tools/FixLengthTool";
import { createLineTool } from "./tools/LineTool";
import { LocalFramePanel, createLocalFrameTool } from "./tools/LocalFrameTool";
import { createPointTool, createTextTool } from "./tools/PointTextTools";
import { createPolylineTool } from "./tools/PolylineTool";
import { SelectionPanel, createSelectTool } from "./tools/SelectTool";
import { createSymbolTool } from "./tools/SymbolTool";
import { createTriangulationTool } from "./tools/TriangulationDialog";
import { createVertexEditTool } from "./tools/VertexEditTool";
import {
  editorUi,
  type EditorTool,
  type EditorUiStore,
  type ToolContext,
  type ToolId,
} from "./ui/editorUi";

/**
 * Width of the palette column: its buttons wrap inside it. Sized by its content, the row of 11
 * tool buttons took ~820 px and left the canvas almost no width on a 1280 px screen.
 */
const PALETTE_COLUMN_WIDTH_PX = 240;

export const TOOL_FACTORIES: Readonly<Record<ToolId, (context: ToolContext) => EditorTool>> = {
  select: createSelectTool,
  vertex: createVertexEditTool,
  line: createLineTool,
  polyline: createPolylineTool,
  point: createPointTool,
  text: createTextTool,
  symbol: createSymbolTool,
  frame: createLocalFrameTool,
  fixlength: createFixLengthTool,
  triangulate: createTriangulationTool,
  baseline: createBaselineOffsetTool,
};

export type EditorWorkspaceProps = Omit<
  EditorCanvasProps,
  "store" | "tool" | "layerFlags" | "onLayerFlagsChange" | "overlay" | "keyboardCursor"
> & {
  store?: StoreApi<EditorStore>;
  ui?: StoreApi<EditorUiStore>;
  snap?: StoreApi<SnapSettingsStore>;
  symbols?: StoreApi<SymbolPickerStore>;
  plan?: StoreApi<PlanStore>;
  /** API client of the local frame panel and the fix-length tool; the same-origin client by default. */
  api?: ApiClient;
};

export function EditorWorkspace({
  store = editorStore,
  ui = editorUi,
  snap = snapSettings,
  symbols = symbolPicker,
  plan = planStore,
  api,
  catalog,
  ...canvasProps
}: EditorWorkspaceProps) {
  const { paperScale } = canvasProps;
  const activeTool = useStore(ui, (state) => state.activeTool);
  const layerFlags = useStore(ui, (state) => state.layerFlags);
  const planOpen = useStore(plan, (state) => state.planId !== null);
  const northDeg = useStore(plan, (state) => state.localFrame?.north_angle_deg ?? 0);
  const readOnly = useStore(plan, (state) => state.readOnly);
  const gridM = useStore(snap, (state) => state.gridM);
  const tool = useMemo(
    () =>
      readOnly
        ? undefined
        : TOOL_FACTORIES[activeTool]({ store, ui, catalog, snap, symbols, paperScale, api }),
    [readOnly, activeTool, store, ui, catalog, snap, symbols, paperScale, api],
  );

  useEffect(() => {
    if (tool === undefined) return;
    const active = tool;
    function onKeyDown(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey || isTypingTarget(event.target)) return;
      if (active.onKeyDown?.(event) === true) event.preventDefault();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [tool]);

  return (
    <div className="editor-workspace" style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          flex: `0 0 ${String(PALETTE_COLUMN_WIDTH_PX)}px`,
        }}
      >
        <Palette ui={ui} catalog={catalog} disabled={readOnly} />
        <HistoryButtons store={store} disabled={readOnly} />
        <KeyboardHelp />
      </div>
      <EditorCanvas
        {...canvasProps}
        catalog={catalog}
        store={store}
        tool={tool}
        layerFlags={layerFlags}
        onLayerFlagsChange={(flags) => ui.getState().setLayerFlags(flags)}
        overlay={planOpen ? <NorthArrow northDeg={northDeg} /> : undefined}
        keyboardCursor={
          tool !== undefined && CURSOR_TOOLS.has(activeTool) ? { step: gridM } : undefined
        }
      />
      {!readOnly && activeTool === "select" && (
        <SelectionPanel store={store} ui={ui} catalog={catalog} />
      )}
      {!readOnly && activeTool === "symbol" && <SymbolPicker picker={symbols} catalog={catalog} />}
      {!readOnly && activeTool === "frame" && (
        <LocalFramePanel ui={ui} store={store} plan={plan} api={api} />
      )}
      {tool?.panel}
      <SnapSettings snap={snap} />
    </div>
  );
}
