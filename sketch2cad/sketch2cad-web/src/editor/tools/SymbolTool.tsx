// Symbol tool (jotdown H2, CU-04, spec 12.2): a click places the symbol chosen in the symbol
// picker at the snapped pointer, with the picker's rotation, scale and attribute values, as a
// `symbol` entity created through the editor store (history and API queue). The entity goes to
// the symbol's catalog `default_layer`, or to the palette's active layer when the default layer
// is not an editable catalog layer; a locked or hidden target layer blocks the placement. A
// missing or invalid attribute (e.g. `evidence_number` of `evidence_marker`) also blocks it and
// the picker shows why. A ghost of the symbol follows the pointer; Esc leaves the tool.
import { useMemo } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { DEFAULT_PAPER_SCALE, SymbolGlyph, type Catalog } from "../canvas/EditorCanvas";
import { parseSymbolSvg } from "../canvas/symbolArt";
import type { CatalogSymbol } from "../canvas/symbols";
import {
  SYMBOL_PICKER_TEXT,
  parseDecimal,
  readPlacement,
  symbolPicker,
  type SymbolPickerStore,
} from "../panels/SymbolPicker";
import type { EntityId, Point2 } from "../store/types";
import {
  editableLayers,
  isDrawable,
  resolveActiveLayer,
  type EditorTool,
  type EditorUiStore,
  type ToolContext,
} from "../ui/editorUi";
import { snappedDrawPoint } from "./LineTool";

/** Opacity of the ghost that previews the symbol under the pointer. */
export const GHOST_OPACITY = 0.5;

/** Catalog symbol chosen in the picker, if any. */
export function pickedSymbol(
  catalog: Catalog,
  picker: StoreApi<SymbolPickerStore>,
): CatalogSymbol | undefined {
  const { symbolId } = picker.getState();
  return catalog.symbols.find((symbol) => symbol.symbol_id === symbolId);
}

/** Layer a new `symbol` entity goes to: its default layer, else the palette's active layer. */
export function symbolTargetLayer(
  state: EditorUiStore,
  catalog: Catalog,
  symbol: CatalogSymbol,
): string | null {
  return editableLayers(catalog).includes(symbol.default_layer)
    ? symbol.default_layer
    : resolveActiveLayer(state, catalog);
}

/**
 * Place the picked symbol at `anchor`; returns the new entity's id, or `null` when the placement
 * is blocked (the reason is recorded in the picker store).
 */
export function placeSymbol(context: ToolContext, anchor: Point2): EntityId | null {
  const picker = context.symbols ?? symbolPicker;
  const symbol = pickedSymbol(context.catalog, picker);
  if (symbol === undefined) {
    picker.getState().block(SYMBOL_PICKER_TEXT.noSymbol);
    return null;
  }
  const ui = context.ui.getState();
  const layer = symbolTargetLayer(ui, context.catalog, symbol);
  if (layer === null || !isDrawable(ui, context.catalog, layer)) {
    picker.getState().block(SYMBOL_PICKER_TEXT.layerNotDrawable(layer ?? symbol.default_layer));
    return null;
  }
  const placement = readPlacement(symbol, picker.getState());
  if (!placement.ok) {
    picker.getState().block(null);
    return null;
  }
  const id = context.store.getState().addEntity({
    type: "symbol",
    layer,
    geometry: {
      kind: "symbol",
      symbol_id: symbol.symbol_id,
      anchor_m: anchor,
      rotation_deg: placement.rotationDeg,
      scale: placement.scale,
      attributes: placement.attributes,
    },
    attributes: {},
  });
  picker.getState().placed();
  return id;
}

function SymbolGhost({
  context,
  picker,
}: {
  context: ToolContext;
  picker: StoreApi<SymbolPickerStore>;
}) {
  const hover = useStore(context.ui, (state) => state.hover);
  const state = useStore(picker);
  const symbol = context.catalog.symbols.find((item) => item.symbol_id === state.symbolId);
  const art = useMemo(() => (symbol === undefined ? null : parseSymbolSvg(symbol.svg)), [symbol]);
  if (hover === null || symbol === undefined || art === null) return null;
  // The ghost follows rotation and scale even while an attribute is still missing.
  const rotation = parseDecimal(state.rotation) ?? 0;
  const scale = parseDecimal(state.scale);
  return (
    <SymbolGlyph
      data-testid="symbol-ghost"
      symbol={symbol}
      art={art}
      geometry={{
        anchor_m: hover,
        rotation_deg: rotation,
        scale: scale !== null && scale > 0 ? scale : 1,
      }}
      paperScale={context.paperScale ?? DEFAULT_PAPER_SCALE}
      opacity={GHOST_OPACITY}
      stroke="currentColor"
      fill="none"
    />
  );
}

export function createSymbolTool(context: ToolContext): EditorTool {
  const { ui } = context;
  const picker = context.symbols ?? symbolPicker;
  return {
    onPointerDown(pointer) {
      const point = snappedDrawPoint(context, pointer, []);
      if (point !== null) placeSymbol(context, point);
      return true;
    },
    onPointerMove(pointer) {
      ui.getState().setHover(snappedDrawPoint(context, pointer, []));
    },
    onKeyDown(event) {
      if (event.key !== "Escape") return false;
      ui.getState().setTool("select");
      return true;
    },
    preview: <SymbolGhost context={context} picker={picker} />,
  };
}
