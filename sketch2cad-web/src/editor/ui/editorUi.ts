// Editor UI store (jotdown H2): active tool, active layer, layer visibility/lock flags and the
// in-progress drawing of the active tool. None of it is part of the document or of the undo
// history; it lives here so that the palette, the tools and the canvas share it.
import { createStore, type StoreApi } from "zustand/vanilla";
import type { CanvasTool, Catalog } from "../canvas/EditorCanvas";
import { layerFlags, type LayerFlagsByName } from "../canvas/layers";
import type { SnapSettingsStore } from "../panels/SnapSettings";
import type { SymbolPickerStore } from "../panels/SymbolPicker";
import type { EditorStore } from "../store/entities";
import type { EntityId, Geometry, Point2 } from "../store/types";

export type ToolId =
  | "select"
  | "vertex"
  | "line"
  | "polyline"
  | "point"
  | "text"
  | "symbol"
  | "frame";

/** Layer reserved for the export sheet (spec 12.1): never drawn on in the editor. */
export const SHEET_LAYER = "L_SHEET";

export interface EditorUiState {
  activeTool: ToolId;
  /** Layer chosen in the palette; `null` means the first editable catalog layer. */
  activeLayer: string | null;
  layerFlags: LayerFlagsByName;
  /** World points placed so far by the active tool (line start, polyline vertices, text anchor). */
  draft: Point2[];
  /** World point under the pointer, for the rubber-band preview. */
  hover: Point2 | null;
}

export interface EditorUiActions {
  /** Switch tool; any in-progress drawing is dropped. */
  setTool(tool: ToolId): void;
  setActiveLayer(layer: string): void;
  setLayerFlags(flags: LayerFlagsByName): void;
  setDraft(draft: Point2[]): void;
  setHover(point: Point2 | null): void;
  cancelDraft(): void;
}

export type EditorUiStore = EditorUiState & EditorUiActions;

export function createEditorUiStore(initial: Partial<EditorUiState> = {}): StoreApi<EditorUiStore> {
  return createStore<EditorUiStore>()((set) => ({
    activeTool: "select",
    activeLayer: null,
    layerFlags: {},
    draft: [],
    hover: null,
    ...initial,
    setTool: (activeTool) => set({ activeTool, draft: [], hover: null }),
    setActiveLayer: (activeLayer) => set({ activeLayer }),
    setLayerFlags: (flags) => set({ layerFlags: flags }),
    setDraft: (draft) => set({ draft }),
    setHover: (hover) => set({ hover }),
    cancelDraft: () => set({ draft: [], hover: null }),
  }));
}

/** The editor's UI store instance. */
export const editorUi = createEditorUiStore();

/** Catalog layers that entities may be put on: all but the sheet layer. */
export function editableLayers(catalog: Catalog): string[] {
  return catalog.layers.map((layer) => layer.name).filter((name) => name !== SHEET_LAYER);
}

/** Whether entities can be drawn on, or moved to, `layer`: editable, visible and unlocked. */
export function isDrawable(
  state: Pick<EditorUiState, "layerFlags">,
  catalog: Catalog,
  layer: string,
): boolean {
  const flags = layerFlags(state.layerFlags, layer);
  return editableLayers(catalog).includes(layer) && flags.visible && !flags.locked;
}

/** The active layer as the palette shows it. */
export function resolveActiveLayer(state: EditorUiState, catalog: Catalog): string | null {
  return state.activeLayer ?? editableLayers(catalog)[0] ?? null;
}

/** Layer new entities go to, or `null` when the active layer cannot be drawn on. */
export function drawingLayer(state: EditorUiState, catalog: Catalog): string | null {
  const layer = resolveActiveLayer(state, catalog);
  return layer !== null && isDrawable(state, catalog, layer) ? layer : null;
}

/** What a tool works with. */
export interface ToolContext {
  store: StoreApi<EditorStore>;
  ui: StoreApi<EditorUiStore>;
  catalog: Catalog;
  /** Snap settings; the editor's `snapSettings` instance when omitted. */
  snap?: StoreApi<SnapSettingsStore>;
  /** Symbol picker choices; the editor's `symbolPicker` instance when omitted. */
  symbols?: StoreApi<SymbolPickerStore>;
  /** Denominator of the plan's paper scale, as the canvas uses it (1:100 when omitted). */
  paperScale?: number;
}

/** A canvas tool that also takes the editor's keys (see EditorWorkspace). */
export interface EditorTool extends CanvasTool {
  /** Return true when the key was used. */
  onKeyDown?(event: KeyboardEvent): boolean;
}

function finite(point: Point2): boolean {
  return Number.isFinite(point[0]) && Number.isFinite(point[1]);
}

/** Copy of a point; `null` when a coordinate is not finite (the API would reject it). */
export function worldPoint(point: Point2): Point2 | null {
  return finite(point) ? [point[0], point[1]] : null;
}

/**
 * Add an entity of `geometry` on the drawing layer through the editor store (history and API
 * queue); returns its id, or `null` when the active layer cannot be drawn on.
 */
export function addOnDrawingLayer(context: ToolContext, geometry: Geometry): EntityId | null {
  const layer = drawingLayer(context.ui.getState(), context.catalog);
  if (layer === null) return null;
  return context.store
    .getState()
    .addEntity({ type: geometry.kind, layer, geometry, attributes: {} });
}
