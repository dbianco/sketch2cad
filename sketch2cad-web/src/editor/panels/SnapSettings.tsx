// Snap settings (jotdown H2): which snaps are on and the grid size, persisted in localStorage
// (`s2c.grid`, `s2c.snap.*`), the panel that edits them, and `resolveSnap`, which applies them to
// a point for the vertex and drawing tools. Storage access is wrapped in try/catch: private modes
// and sandboxed frames throw on localStorage, and then the defaults are used.
import { useState } from "react";
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";
import { layerFlags } from "../canvas/layers";
import {
  ENDPOINT_SNAP_RADIUS_PX,
  collectSnapCandidates,
  isValidGridSize,
  snapPoint,
  type SnapKind,
  type SnapResult,
} from "../snapping/snap";
import type { EntityId, Point2 } from "../store/types";
import type { ToolContext } from "../ui/editorUi";

/** localStorage key of the grid size in metres. */
export const GRID_STORAGE_KEY = "s2c.grid";
/** localStorage key prefix of the snap toggles: `s2c.snap.endpoint`, `.orthogonal`, `.grid`. */
export const SNAP_STORAGE_PREFIX = "s2c.snap.";

/**
 * Default grid size: 0.1 m. Field sketches are measured to the centimetre-decimetre, so a 10 cm
 * grid tidies hand-placed vertices without moving them visibly at plan scale. Grid snapping is
 * off by default because it quantizes every edit; endpoint and orthogonal snapping are on.
 */
export const DEFAULT_GRID_M = 0.1;
export const DEFAULT_SNAP_ENABLED: Readonly<Record<SnapKind, boolean>> = {
  endpoint: true,
  orthogonal: true,
  grid: false,
};

const SNAP_KINDS: readonly SnapKind[] = ["endpoint", "orthogonal", "grid"];

export const SNAP_TEXT = {
  panel: "Ajustes de snapping",
  endpoint: "Ajustar a extremos",
  orthogonal: "Ajustar a ortogonal (0° y 90°)",
  grid: "Ajustar a grilla",
  gridSize: "Tamaño de grilla (m)",
  invalidGrid: "El tamaño de grilla debe ser un número mayor que 0.",
  applied: {
    endpoint: "Ajuste a extremo",
    orthogonal: "Ajuste ortogonal",
    grid: "Ajuste a grilla",
  } satisfies Record<SnapKind, string>,
} as const;

export interface SnapSettingsState {
  enabled: Record<SnapKind, boolean>;
  gridM: number;
}

export interface SnapSettingsActions {
  setEnabled(kind: SnapKind, enabled: boolean): void;
  /** Set and persist the grid size; returns false (and changes nothing) when it is invalid. */
  setGridM(gridM: number): boolean;
}

export type SnapSettingsStore = SnapSettingsState & SnapSettingsActions;

/** The browser's localStorage, or null when it is unavailable or access throws. */
export function defaultStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function read(storage: Storage | null, key: string): string | null {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function write(storage: Storage | null, key: string, value: string): void {
  try {
    storage?.setItem(key, value);
  } catch {
    // Quota or access errors: the setting still applies for this session.
  }
}

/** Stored grid size, or DEFAULT_GRID_M when absent, invalid or unreadable. */
export function loadGridSize(storage: Storage | null = defaultStorage()): number {
  const raw = read(storage, GRID_STORAGE_KEY);
  const value = raw === null || raw.trim() === "" ? NaN : Number(raw);
  return isValidGridSize(value) ? value : DEFAULT_GRID_M;
}

export function loadSnapSettings(storage: Storage | null = defaultStorage()): SnapSettingsState {
  const enabled = { ...DEFAULT_SNAP_ENABLED };
  for (const kind of SNAP_KINDS) {
    const raw = read(storage, SNAP_STORAGE_PREFIX + kind);
    if (raw === "true" || raw === "false") enabled[kind] = raw === "true";
  }
  return { enabled, gridM: loadGridSize(storage) };
}

export function createSnapSettingsStore(
  storage: Storage | null = defaultStorage(),
): StoreApi<SnapSettingsStore> {
  return createStore<SnapSettingsStore>()((set, get) => ({
    ...loadSnapSettings(storage),
    setEnabled(kind, enabled) {
      write(storage, SNAP_STORAGE_PREFIX + kind, String(enabled));
      set({ enabled: { ...get().enabled, [kind]: enabled } });
    },
    setGridM(gridM) {
      if (!isValidGridSize(gridM)) return false;
      write(storage, GRID_STORAGE_KEY, String(gridM));
      set({ gridM });
      return true;
    },
  }));
}

/** The editor's snap settings instance. */
export const snapSettings = createSnapSettingsStore();

export interface ResolveSnapInput {
  /** Neighbouring vertices of the moved or new vertex (orthogonal snapping). */
  anchors: readonly Point2[];
  /** Entity being edited: its own vertices are not endpoint candidates. */
  exclude: EntityId | null;
  /** Screen pixels per metre, to convert ENDPOINT_SNAP_RADIUS_PX. */
  scale: number;
}

/**
 * Snap `point` with the context's settings. Endpoint candidates are the vertices of the other
 * live entities on visible layers (a locked layer can still be snapped to).
 */
export function resolveSnap(context: ToolContext, point: Point2, input: ResolveSnapInput): SnapResult {
  const settings = (context.snap ?? snapSettings).getState();
  const flags = context.ui.getState().layerFlags;
  const candidates = settings.enabled.endpoint
    ? collectSnapCandidates(
        context.store.getState().entities,
        input.exclude,
        (layer) => layerFlags(flags, layer).visible,
      )
    : [];
  return snapPoint(point, {
    ...settings.enabled,
    candidates,
    radiusWorld: input.scale > 0 ? ENDPOINT_SNAP_RADIUS_PX / input.scale : 0,
    anchors: input.anchors,
    gridM: settings.gridM,
  });
}

export interface SnapSettingsProps {
  snap: StoreApi<SnapSettingsStore>;
}

/** Toggles of each snap and the grid size; every control has a Spanish accessible label. */
export function SnapSettings({ snap }: SnapSettingsProps) {
  const enabled = useStore(snap, (state) => state.enabled);
  const gridM = useStore(snap, (state) => state.gridM);
  const [gridText, setGridText] = useState(() => String(gridM));
  const [invalid, setInvalid] = useState(false);

  function onGridChange(text: string) {
    setGridText(text);
    const valid = text.trim() !== "" && snap.getState().setGridM(Number(text));
    setInvalid(!valid);
  }

  return (
    <section className="editor-snap-settings" aria-label={SNAP_TEXT.panel}>
      <h2>{SNAP_TEXT.panel}</h2>
      {SNAP_KINDS.map((kind) => (
        <label key={kind} style={{ display: "block" }}>
          <input
            type="checkbox"
            aria-label={SNAP_TEXT[kind]}
            checked={enabled[kind]}
            onChange={(event) => snap.getState().setEnabled(kind, event.target.checked)}
          />{" "}
          {SNAP_TEXT[kind]}
        </label>
      ))}
      <label style={{ display: "block" }}>
        {SNAP_TEXT.gridSize}{" "}
        <input
          type="number"
          aria-label={SNAP_TEXT.gridSize}
          aria-invalid={invalid}
          min={0}
          step="any"
          value={gridText}
          onChange={(event) => onGridChange(event.target.value)}
        />
      </label>
      {invalid && <p role="alert">{SNAP_TEXT.invalidGrid}</p>}
    </section>
  );
}
