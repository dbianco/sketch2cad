// Suggestion layer of the editor canvas (US-12, REQ-17, jotdown H5): the pending suggestions
// (`review_status` "suggested", see `isSuggestion`) in the suggestion colour, on top of the
// layers. Those with a confidence below `suggestions.min_confidence_highlight` carry the
// `suggestion--low-confidence` class and a highlighter halo. A selected suggestion takes the
// selection colour. It renders nothing unless AI assistance is on (`GET /config/features`).
//
// Pointer: each suggestion carries `data-entity-id`, so the active tool sees it as the canvas
// sees any entity (the Select tool selects it); without a tool a click selects it. A transparent
// wide stroke makes the thin lines easier to hit. Accepting and rejecting is done from the batch
// panel (BatchPanel.tsx).
import { memo, type PointerEvent } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import type { ApiClient } from "../../api/client";
import {
  SELECTION_COLOR,
  STROKE_WIDTH_PX,
  isSuggestion,
  svgPoints,
  useCanvasDelegate,
} from "../../editor/canvas/EditorCanvas";
import type { EditorStore } from "../../editor/store/entities";
import type { EditorState, EntityId } from "../../editor/store/types";
import { isLowConfidence, useAiAssistEnabled, useHighlightThreshold } from "./aiAssist";

/** Colour of pending suggestions: a violet no catalog layer uses. */
export const SUGGESTION_COLOR = "#6a3fd4";
/** Highlighter halo under low-confidence suggestions. */
export const LOW_CONFIDENCE_HALO_COLOR = "#ffd400";
export const LOW_CONFIDENCE_CLASS = "suggestion--low-confidence";
const HALO_WIDTH_PX = 8;
const HIT_WIDTH_PX = 10;

const suggestionIdsCache = new WeakMap<EditorState["entities"], EntityId[]>();

/** Ids of the live pending suggestions, in drawing order; stable while the entities are. */
export function selectSuggestionIds(state: EditorState): EntityId[] {
  let ids = suggestionIdsCache.get(state.entities);
  if (ids === undefined) {
    const { byId, order } = state.entities;
    ids = order.filter((id) => byId[id]?.deleted === false && isSuggestion(byId[id]));
    suggestionIdsCache.set(state.entities, ids);
  }
  return ids;
}

interface SuggestionElementProps {
  id: EntityId;
  store: StoreApi<EditorStore>;
  threshold: number;
}

const SuggestionElement = memo(function SuggestionElement({
  id,
  store,
  threshold,
}: SuggestionElementProps) {
  const entity = useStore(store, (state) => state.entities.byId[id]);
  const selected = useStore(store, (state) => state.selection.includes(id));
  const delegate = useCanvasDelegate();
  if (entity === undefined || entity.deleted || !isSuggestion(entity)) return null;
  const geometry = entity.geometry;
  if (geometry.kind !== "line" && geometry.kind !== "polyline") return null;
  const Shape = geometry.kind === "polyline" && geometry.closed ? "polygon" : "polyline";
  const points = svgPoints(geometry.coords_m);
  const low = isLowConfidence(entity.remote?.confidence, threshold);
  const onPointerDown = (event: PointerEvent<SVGGElement>) => {
    if (event.button !== 0 || delegate) return;
    event.stopPropagation();
    store.getState().select([id]);
  };
  return (
    <g
      data-entity-id={id}
      data-kind={geometry.kind}
      data-suggestion="true"
      className={low ? `suggestion ${LOW_CONFIDENCE_CLASS}` : "suggestion"}
      color={selected ? SELECTION_COLOR : undefined}
      onPointerDown={onPointerDown}
    >
      {low && (
        <Shape
          points={points}
          stroke={LOW_CONFIDENCE_HALO_COLOR}
          strokeOpacity={0.6}
          strokeWidth={HALO_WIDTH_PX}
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
          pointerEvents="none"
        />
      )}
      <Shape points={points} vectorEffect="non-scaling-stroke" />
      <Shape
        points={points}
        stroke="transparent"
        strokeWidth={HIT_WIDTH_PX}
        vectorEffect="non-scaling-stroke"
        pointerEvents="stroke"
      />
    </g>
  );
});

export interface SuggestionLayerProps {
  store: StoreApi<EditorStore>;
  api: Pick<ApiClient, "get">;
}

/** Must be rendered inside EditorCanvas (its `suggestionLayer` slot). */
export function SuggestionLayer({ store, api }: SuggestionLayerProps) {
  const enabled = useAiAssistEnabled(api);
  const threshold = useHighlightThreshold(api, !enabled);
  const ids = useStore(store, selectSuggestionIds);
  if (!enabled) return null;
  return (
    <g
      data-testid="suggestion-layer"
      color={SUGGESTION_COLOR}
      stroke="currentColor"
      fill="none"
      strokeWidth={STROKE_WIDTH_PX}
    >
      {ids.map((id) => (
        <SuggestionElement key={id} id={id} store={store} threshold={threshold} />
      ))}
    </g>
  );
}
