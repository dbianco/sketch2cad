// Layer model of the editor canvas: catalog layers (spec section 12.1) with their web color,
// per-layer visibility and lock, and the grouping of entities by layer in drawing order.
import type { components } from "../../api/schema";
import type { EditorEntity, EntityId } from "../store/types";

export type CatalogLayer = components["schemas"]["Layer"];

/**
 * Web colors of the AutoCAD Color Index entries used by the default layers. They are the
 * standard ACI colors adapted to the editor's light background: ACI 7 ("white/black") is black
 * on a light background as in AutoCAD, and yellow (2) and cyan (4) are darkened to stay legible.
 */
export const ACI_WEB_COLORS: Readonly<Record<number, string>> = {
  1: "#e00000",
  2: "#b39200",
  3: "#00a000",
  4: "#008b9a",
  5: "#0030e0",
  6: "#c000c0",
  7: "#000000",
  8: "#808080",
  9: "#a0a0a0",
};

/** Color of an ACI index outside ACI_WEB_COLORS, and of layers missing from the catalog. */
export const FALLBACK_LAYER_COLOR = "#606060";

export function aciToCss(aci: number): string {
  return ACI_WEB_COLORS[aci] ?? FALLBACK_LAYER_COLOR;
}

/** UI state of one layer; not part of the document nor of the undo history. */
export interface LayerFlags {
  visible: boolean;
  locked: boolean;
}

export type LayerFlagsByName = Readonly<Record<string, LayerFlags>>;

export const DEFAULT_LAYER_FLAGS: LayerFlags = { visible: true, locked: false };

/** A layer as the canvas and the layer panel show it. */
export interface LayerView {
  name: string;
  description: string;
  color: string;
  /** False for a layer used by entities but missing from the catalog. */
  inCatalog: boolean;
}

export function layerFlags(flags: LayerFlagsByName, name: string): LayerFlags {
  return flags[name] ?? DEFAULT_LAYER_FLAGS;
}

export function toggleVisibility(flags: LayerFlagsByName, name: string): LayerFlagsByName {
  const current = layerFlags(flags, name);
  return { ...flags, [name]: { ...current, visible: !current.visible } };
}

export function toggleLock(flags: LayerFlagsByName, name: string): LayerFlagsByName {
  const current = layerFlags(flags, name);
  return { ...flags, [name]: { ...current, locked: !current.locked } };
}

/**
 * Layers in drawing order: the catalog order first (the first one is drawn at the bottom),
 * then the layers that entities use but the catalog lacks, sorted by name.
 */
export function layerViews(
  catalogLayers: readonly CatalogLayer[],
  usedLayers: Iterable<string>,
): LayerView[] {
  const views: LayerView[] = catalogLayers.map((layer) => ({
    name: layer.name,
    description: layer.description,
    color: aciToCss(layer.aci_color),
    inCatalog: true,
  }));
  const known = new Set(views.map((view) => view.name));
  const unknown = [...new Set(usedLayers)].filter((name) => !known.has(name)).sort();
  for (const name of unknown) {
    views.push({ name, description: "", color: FALLBACK_LAYER_COLOR, inCatalog: false });
  }
  return views;
}

/** Entity ids per layer, each list in the document's drawing order. */
export function groupByLayer(
  ids: readonly EntityId[],
  byId: Readonly<Record<EntityId, EditorEntity>>,
): Map<string, EntityId[]> {
  const groups = new Map<string, EntityId[]>();
  for (const id of ids) {
    const entity = byId[id];
    if (entity === undefined) continue;
    const group = groups.get(entity.layer);
    if (group === undefined) groups.set(entity.layer, [id]);
    else group.push(id);
  }
  return groups;
}

export function sameIds(a: readonly EntityId[], b: readonly EntityId[]): boolean {
  return a.length === b.length && a.every((id, index) => id === b[index]);
}
