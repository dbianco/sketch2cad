// Tool palette (jotdown H2 `Palette`, prototype Editor.dc.html): one button per tool, with its
// keyboard shortcut, and the active-layer picker. Shortcuts are single keys without modifiers and
// are ignored while typing in a form field. Every control is a native button or select, so the
// palette is reachable with Tab (spec 17, WCAG 2.1 AA).
import { useEffect } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import type { Catalog } from "../canvas/EditorCanvas";
import {
  editableLayers,
  isDrawable,
  resolveActiveLayer,
  type EditorUiStore,
  type ToolId,
} from "../ui/editorUi";

export interface ToolButton {
  id: ToolId;
  /** Accessible name. */
  label: string;
  /** Visible caption, as in the prototype. */
  caption: string;
  /** Shortcut key (upper case; the lower-case key works too). */
  shortcut: string;
}

export const TOOL_BUTTONS: readonly ToolButton[] = [
  { id: "select", label: "Seleccionar", caption: "Elegir", shortcut: "V" },
  { id: "vertex", label: "Editar vértices", caption: "Vértices", shortcut: "E" },
  { id: "line", label: "Línea", caption: "Línea", shortcut: "L" },
  { id: "polyline", label: "Polilínea", caption: "Polilín.", shortcut: "P" },
  { id: "point", label: "Punto", caption: "Punto", shortcut: "O" },
  { id: "text", label: "Texto", caption: "Texto", shortcut: "T" },
];

export const PALETTE_TEXT = {
  palette: "Paleta de herramientas",
  tools: "Herramientas",
  layer: "Capa activa",
  locked: (layer: string) => `${layer} (bloqueada u oculta)`,
  notDrawable: "La capa activa está bloqueada u oculta: no se puede dibujar en ella.",
} as const;

/** True when keys typed at `target` are text input, not shortcuts. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

/** Tool of a shortcut key event, if any. */
export function shortcutTool(event: KeyboardEvent): ToolId | undefined {
  if (event.ctrlKey || event.metaKey || event.altKey || isTypingTarget(event.target))
    return undefined;
  return TOOL_BUTTONS.find((tool) => tool.shortcut === event.key.toUpperCase())?.id;
}

export interface PaletteProps {
  ui: StoreApi<EditorUiStore>;
  catalog: Catalog;
}

export function Palette({ ui, catalog }: PaletteProps) {
  const activeTool = useStore(ui, (state) => state.activeTool);
  const layerFlags = useStore(ui, (state) => state.layerFlags);
  const activeLayer = useStore(ui, (state) => resolveActiveLayer(state, catalog));

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const tool = shortcutTool(event);
      if (tool === undefined) return;
      event.preventDefault();
      ui.getState().setTool(tool);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [ui]);

  const drawable = activeLayer !== null && isDrawable({ layerFlags }, catalog, activeLayer);

  return (
    <aside className="editor-palette" aria-label={PALETTE_TEXT.palette}>
      <div role="group" aria-label={PALETTE_TEXT.tools}>
        {TOOL_BUTTONS.map((tool) => (
          <button
            key={tool.id}
            type="button"
            aria-label={tool.label}
            aria-pressed={activeTool === tool.id}
            aria-keyshortcuts={tool.shortcut}
            title={`${tool.label} (${tool.shortcut})`}
            onClick={() => ui.getState().setTool(tool.id)}
          >
            {tool.caption}
          </button>
        ))}
      </div>
      <label>
        {PALETTE_TEXT.layer}{" "}
        <select
          aria-label={PALETTE_TEXT.layer}
          value={activeLayer ?? ""}
          onChange={(event) => ui.getState().setActiveLayer(event.target.value)}
        >
          {editableLayers(catalog).map((name) => {
            const usable = isDrawable({ layerFlags }, catalog, name);
            return (
              <option key={name} value={name} disabled={!usable && name !== activeLayer}>
                {usable ? name : PALETTE_TEXT.locked(name)}
              </option>
            );
          })}
        </select>
      </label>
      {!drawable && <p role="status">{PALETTE_TEXT.notDrawable}</p>}
    </aside>
  );
}
