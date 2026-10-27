// Select tool (jotdown H2, CU-04): click selects an entity, Shift + click adds or removes it,
// Delete/Backspace deletes the selection, Esc clears it. The selection panel moves the selection
// to another layer (reclassify) and, for a single dimension, edits its declared value (relabel,
// undoable). Entities on locked or hidden layers are left alone. A press on the background is
// declined, so the canvas pans as in the viewer.
import { useState } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import type { Catalog } from "../canvas/EditorCanvas";
import { layerFlags as flagsOf } from "../canvas/layers";
import type { EditorStore } from "../store/entities";
import type { EditorEntity, EntityId } from "../store/types";
import { DISTANCE_MESSAGE, parseDistance } from "../util/parseDistance";
import {
  editableLayers,
  isDrawable,
  type EditorTool,
  type EditorUiStore,
  type ToolContext,
} from "../ui/editorUi";

export const SELECT_TEXT = {
  panel: "Selección",
  none: "Nada seleccionado",
  count: (n: number) =>
    n === 1 ? "1 entidad seleccionada" : `${String(n)} entidades seleccionadas`,
  layer: "Capa de la selección",
  mixed: "Varias capas",
  remove: "Eliminar selección",
  removeShort: "Eliminar",
  declared: "Valor declarado de la cota (m)",
  saveDeclared: "Guardar valor",
} as const;

/** Selected entities that may be edited: live and on a visible, unlocked layer. */
function editableSelection(context: ToolContext): EntityId[] {
  const { selection, entities } = context.store.getState();
  const flags = context.ui.getState().layerFlags;
  return selection.filter((id) => {
    const entity = entities.byId[id];
    if (entity === undefined || entity.deleted) return false;
    const layer = flagsOf(flags, entity.layer);
    return layer.visible && !layer.locked;
  });
}

export function deleteSelection(context: ToolContext): void {
  const store = context.store.getState();
  for (const id of editableSelection(context)) {
    store.deleteEntity(id);
  }
  context.store.getState().clearSelection();
}

/** Move the editable part of the selection to `layer`, one `reclassify` per entity. */
export function reclassifySelection(context: ToolContext, layer: string): void {
  if (!isDrawable(context.ui.getState(), context.catalog, layer)) return;
  for (const id of editableSelection(context)) {
    const state = context.store.getState();
    if (state.entities.byId[id]?.layer !== layer) state.reclassify(id, layer);
  }
}

export function createSelectTool(context: ToolContext): EditorTool {
  return {
    onPointerDown(pointer) {
      const id = pointer.entityId;
      if (id === null) return false;
      const state = context.store.getState();
      const entity = state.entities.byId[id];
      if (entity === undefined || entity.deleted) return false;
      const flags = flagsOf(context.ui.getState().layerFlags, entity.layer);
      if (!flags.visible || flags.locked) return false;
      if (!pointer.shiftKey) state.select([id]);
      else if (state.selection.includes(id)) state.select(state.selection.filter((s) => s !== id));
      else state.select([...state.selection, id]);
      return true;
    },
    onKeyDown(event) {
      const state = context.store.getState();
      if (event.key === "Escape") {
        state.clearSelection();
        return true;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && state.selection.length > 0) {
        deleteSelection(context);
        return true;
      }
      return false;
    },
  };
}

export interface SelectionPanelProps {
  store: StoreApi<EditorStore>;
  ui: StoreApi<EditorUiStore>;
  catalog: Catalog;
}

/** Selection summary, layer dropdown (reclassify) and delete button. */
export function SelectionPanel({ store, ui, catalog }: SelectionPanelProps) {
  const selection = useStore(store, (state) => state.selection);
  const byId = useStore(store, (state) => state.entities.byId);
  const layerFlags = useStore(ui, (state) => state.layerFlags);
  const context: ToolContext = { store, ui, catalog };
  const layers = new Set(selection.map((id) => byId[id]?.layer).filter((l) => l !== undefined));
  const common = layers.size === 1 ? [...layers][0] : undefined;
  const options = editableLayers(catalog);
  if (common !== undefined && !options.includes(common)) options.push(common);

  return (
    <section className="editor-selection-panel" aria-label={SELECT_TEXT.panel}>
      <h2>{SELECT_TEXT.panel}</h2>
      {selection.length === 0 ? (
        <p>{SELECT_TEXT.none}</p>
      ) : (
        <>
          <p>{SELECT_TEXT.count(selection.length)}</p>
          <label>
            {SELECT_TEXT.layer}{" "}
            <select
              aria-label={SELECT_TEXT.layer}
              value={common ?? ""}
              onChange={(event) => reclassifySelection(context, event.target.value)}
            >
              {common === undefined && (
                <option value="" disabled>
                  {SELECT_TEXT.mixed}
                </option>
              )}
              {options.map((name) => (
                <option
                  key={name}
                  value={name}
                  disabled={!isDrawable({ layerFlags }, catalog, name)}
                >
                  {name}
                </option>
              ))}
            </select>
          </label>{" "}
          <button
            type="button"
            aria-label={SELECT_TEXT.remove}
            onClick={() => deleteSelection(context)}
          >
            {SELECT_TEXT.removeShort}
          </button>
          {selection.length === 1 && (
            <DeclaredValueForm
              key={selection[0]}
              entity={byId[selection[0] as EntityId]}
              context={context}
            />
          )}
        </>
      )}
    </section>
  );
}

/** Declared value of a selected dimension; saving relabels it with the new value. */
function DeclaredValueForm({
  entity,
  context,
}: {
  entity: EditorEntity | undefined;
  context: ToolContext;
}) {
  const geometry = entity?.geometry;
  const declared = geometry?.kind === "dimension" ? (geometry.declared_value_m ?? null) : null;
  const [text, setText] = useState(() =>
    declared === null ? "" : declared.toFixed(2).replace(".", ","),
  );
  const [error, setError] = useState(false);
  if (entity === undefined || geometry?.kind !== "dimension") return null;
  if (!editableSelection(context).includes(entity.id)) return null;
  const dimension = geometry;
  const id = entity.id;
  return (
    <form
      className="editor-dimension-value"
      onSubmit={(event) => {
        event.preventDefault();
        const value = parseDistance(text);
        setError(value === null);
        if (value === null || value === declared) return;
        context.store.getState().relabel(id, {
          geometry: { ...dimension, declared_value_m: value },
        });
      }}
    >
      <label>
        {SELECT_TEXT.declared}{" "}
        <input
          type="text"
          inputMode="decimal"
          value={text}
          aria-invalid={error}
          onChange={(event) => setText(event.target.value)}
        />
      </label>{" "}
      <button type="submit">{SELECT_TEXT.saveDeclared}</button>
      {error && <p role="alert">{DISTANCE_MESSAGE}</p>}
    </form>
  );
}
