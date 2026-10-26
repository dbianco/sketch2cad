// Tool palette (jotdown H2 `Palette`, prototype Editor.dc.html): one button per tool, with its
// keyboard shortcut, and the active-layer picker. Shortcuts are single keys without modifiers and
// are ignored while typing in a form field. Every control is a native button or select, so the
// palette is reachable with Tab (spec 17, WCAG 2.1 AA).
import { useEffect } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { TOOL_SHORTCUTS } from "../a11y/shortcuts";
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
  /**
   * Visible caption, as in the prototype but never an abbreviation of the label: the visible
   * text must be part of the accessible name (WCAG 2.1 SC 2.5.3, axe label-content-name-mismatch).
   */
  caption: string;
  /** Shortcut key (upper case; the lower-case key works too). */
  shortcut: string;
}

export const TOOL_BUTTONS: readonly ToolButton[] = [
  { id: "select", label: "Seleccionar", caption: "Seleccionar", shortcut: TOOL_SHORTCUTS.select },
  { id: "vertex", label: "Editar vértices", caption: "Vértices", shortcut: TOOL_SHORTCUTS.vertex },
  { id: "line", label: "Línea", caption: "Línea", shortcut: TOOL_SHORTCUTS.line },
  { id: "polyline", label: "Polilínea", caption: "Polilínea", shortcut: TOOL_SHORTCUTS.polyline },
  { id: "point", label: "Punto", caption: "Punto", shortcut: TOOL_SHORTCUTS.point },
  { id: "text", label: "Texto", caption: "Texto", shortcut: TOOL_SHORTCUTS.text },
  { id: "symbol", label: "Símbolo", caption: "Símbolo", shortcut: TOOL_SHORTCUTS.symbol },
  { id: "frame", label: "Sistema local", caption: "Sistema local", shortcut: TOOL_SHORTCUTS.frame },
  {
    id: "fixlength",
    label: "Fijar longitud",
    caption: "Fijar longitud",
    shortcut: TOOL_SHORTCUTS.fixlength,
  },
  {
    id: "triangulate",
    label: "Triangulación",
    caption: "Triangulación",
    shortcut: TOOL_SHORTCUTS.triangulate,
  },
  {
    id: "baseline",
    label: "Base + offset",
    caption: "Base + offset",
    shortcut: TOOL_SHORTCUTS.baseline,
  },
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
  /** Read-only plan: every tool button and the shortcuts are disabled. */
  disabled?: boolean;
}

export function Palette({ ui, catalog, disabled = false }: PaletteProps) {
  const activeTool = useStore(ui, (state) => state.activeTool);
  const layerFlags = useStore(ui, (state) => state.layerFlags);
  const activeLayer = useStore(ui, (state) => resolveActiveLayer(state, catalog));

  useEffect(() => {
    if (disabled) return;
    function onKeyDown(event: KeyboardEvent) {
      const tool = shortcutTool(event);
      if (tool === undefined) return;
      event.preventDefault();
      ui.getState().setTool(tool);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [ui, disabled]);

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
            disabled={disabled}
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
          disabled={disabled}
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
