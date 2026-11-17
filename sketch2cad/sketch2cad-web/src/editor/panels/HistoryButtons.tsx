// Undo/redo controls of the editor (jotdown H2, CU-04): the "Deshacer" and "Rehacer" buttons and
// their shortcuts, Ctrl/Cmd + Z and Shift + Ctrl/Cmd + Z. Both run the editor store's undo/redo
// (store/history.ts), which also fixes the API queue. The shortcuts are ignored while typing in
// a form field, so the field keeps its native undo. A read-only plan disables both.
import { useEffect } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { selectCanRedo, selectCanUndo, type EditorStore } from "../store/entities";
import { isTypingTarget } from "./Palette";

export const HISTORY_TEXT = {
  group: "Historial",
  undo: "Deshacer",
  redo: "Rehacer",
} as const;

export type HistoryAction = "undo" | "redo";

/** History action of a key event: Ctrl/Cmd + Z undoes, with Shift it redoes. */
export function historyShortcut(event: KeyboardEvent): HistoryAction | undefined {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || isTypingTarget(event.target)) {
    return undefined;
  }
  if (event.key.toLowerCase() !== "z") return undefined;
  return event.shiftKey ? "redo" : "undo";
}

export interface HistoryButtonsProps {
  store: StoreApi<EditorStore>;
  /** Read-only plan: the buttons and the shortcuts are disabled. */
  disabled?: boolean;
}

export function HistoryButtons({ store, disabled = false }: HistoryButtonsProps) {
  const canUndo = useStore(store, selectCanUndo);
  const canRedo = useStore(store, selectCanRedo);

  useEffect(() => {
    if (disabled) return;
    function onKeyDown(event: KeyboardEvent) {
      const action = historyShortcut(event);
      if (action === undefined) return;
      event.preventDefault();
      store.getState()[action]();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [store, disabled]);

  return (
    <div role="group" aria-label={HISTORY_TEXT.group}>
      <button
        type="button"
        aria-keyshortcuts="Control+Z Meta+Z"
        disabled={disabled || !canUndo}
        onClick={() => store.getState().undo()}
      >
        {HISTORY_TEXT.undo}
      </button>
      <button
        type="button"
        aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z"
        disabled={disabled || !canRedo}
        onClick={() => store.getState().redo()}
      >
        {HISTORY_TEXT.redo}
      </button>
    </div>
  );
}
