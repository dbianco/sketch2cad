// Vertex edit tool (jotdown H2, CU-04, US-06): a click selects an entity and shows a handle on
// each of its vertices; dragging a handle moves that vertex with snapping (snap.ts) and a dashed
// preview plus an indicator of the snap that applies. The release commits ONE history entry:
// `setGeometry(..., snap_endpoint | snap_orthogonal | snap_grid)` when a snap decided the point,
// `moveVertex` (operation `move_vertex`) otherwise. Esc cancels the drag.
//
// Handles are buttons laid over the canvas: Tab reaches them, and the arrow keys move the focused
// vertex by one grid step (Shift: FINE_NUDGE_FACTOR of it) as a `move_vertex`. They ignore the
// pointer (`pointer-events: none`); presses are matched to a handle by screen distance instead,
// so the canvas keeps receiving them. Entities on hidden or locked layers are not edited.
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";
import { svgPoints, type CanvasPointer } from "../canvas/EditorCanvas";
import { layerFlags as flagsOf, type LayerFlagsByName } from "../canvas/layers";
import { worldToScreen, type Viewport } from "../canvas/viewport";
import { SNAP_TEXT, resolveSnap, snapSettings } from "../panels/SnapSettings";
import {
  geometryVertices,
  neighbourVertices,
  snapOperation,
  withVertex,
  type SnapKind,
} from "../snapping/snap";
import type { EditorEntity, EntityId, Point2 } from "../store/types";
import { worldPoint, type EditorTool, type ToolContext } from "../ui/editorUi";
import { apart } from "./LineTool";

/** Screen distance, in px, within which a press grabs a vertex handle. */
export const HANDLE_HIT_PX = 8;
/** Side, in px, of a drawn vertex handle. */
export const HANDLE_SIZE_PX = 10;
/** Shift + arrow moves a vertex by this fraction of the grid step. */
export const FINE_NUDGE_FACTOR = 0.1;

export const VERTEX_TEXT = {
  handles: "Vértices de la entidad seleccionada",
  handle: (index: number, count: number) => `Vértice ${String(index + 1)} de ${String(count)}`,
  hint: "Flechas: mover un paso de grilla; Mayús + flechas: paso fino",
} as const;

const ARROW_STEPS: Readonly<Record<string, Point2>> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, 1],
  ArrowDown: [0, -1],
};

interface VertexDrag {
  entityId: EntityId;
  index: number;
  /** World point of the press, to tell a click on a handle from a drag. */
  start: Point2;
  /** Snapped position of the vertex for the preview. */
  point: Point2;
  kind: SnapKind | null;
}

interface DragState {
  drag: VertexDrag | null;
}

function editable(entity: EditorEntity | undefined, flags: LayerFlagsByName): entity is EditorEntity {
  if (entity === undefined || entity.deleted) return false;
  const layer = flagsOf(flags, entity.layer);
  return layer.visible && !layer.locked;
}

/** The entity whose vertices are edited: the first editable one in the selection. */
function targetOf(
  selection: readonly EntityId[],
  byId: Readonly<Record<EntityId, EditorEntity>>,
  flags: LayerFlagsByName,
): EditorEntity | null {
  for (const id of selection) {
    const entity = byId[id];
    if (editable(entity, flags)) return entity;
  }
  return null;
}

function contextTarget(context: ToolContext): EditorEntity | null {
  const { selection, entities } = context.store.getState();
  return targetOf(selection, entities.byId, context.ui.getState().layerFlags);
}

/** Index of the vertex of `entity` nearest to `world` within HANDLE_HIT_PX, or null. */
export function hitVertex(entity: EditorEntity, world: Point2, scale: number): number | null {
  let best: number | null = null;
  let bestPx = Infinity;
  for (const [index, vertex] of geometryVertices(entity.geometry).entries()) {
    const px = Math.hypot(vertex[0] - world[0], vertex[1] - world[1]) * scale;
    if (px <= HANDLE_HIT_PX && px < bestPx) {
      best = index;
      bestPx = px;
    }
  }
  return best;
}

export function createVertexEditTool(context: ToolContext): EditorTool {
  const dragStore = createStore<DragState>()(() => ({ drag: null }));
  const snap = context.snap ?? snapSettings;

  function snapFor(entity: EditorEntity, index: number, raw: Point2, scale: number) {
    return resolveSnap(context, raw, {
      anchors: neighbourVertices(entity.geometry, index),
      exclude: entity.id,
      scale,
    });
  }

  function commit(drag: VertexDrag, pointer: CanvasPointer): void {
    const raw = worldPoint(pointer.world);
    const entity = context.store.getState().entities.byId[drag.entityId];
    if (raw === null || !apart(drag.start, raw, pointer.scale)) return;
    if (!editable(entity, context.ui.getState().layerFlags)) return;
    const result = snapFor(entity, drag.index, raw, pointer.scale);
    const current = geometryVertices(entity.geometry)[drag.index];
    if (current !== undefined && current[0] === result.point[0] && current[1] === result.point[1]) {
      return;
    }
    const store = context.store.getState();
    if (result.kind === null) {
      store.moveVertex(entity.id, drag.index, result.point);
    } else {
      const geometry = withVertex(entity.geometry, drag.index, result.point);
      store.setGeometry(entity.id, geometry, snapOperation(result.kind));
    }
  }

  function nudge(event: KeyboardEvent): boolean {
    const step = ARROW_STEPS[event.key];
    const target = event.target instanceof HTMLElement ? event.target : null;
    const id = target?.dataset.vertexEntity;
    const index = Number(target?.dataset.vertexIndex);
    if (step === undefined || id === undefined || !Number.isInteger(index)) return false;
    const entity = context.store.getState().entities.byId[id];
    if (!editable(entity, context.ui.getState().layerFlags)) return false;
    const vertex = geometryVertices(entity.geometry)[index];
    if (vertex === undefined) return false;
    const distance = snap.getState().gridM * (event.shiftKey ? FINE_NUDGE_FACTOR : 1);
    context.store
      .getState()
      .moveVertex(id, index, [vertex[0] + step[0] * distance, vertex[1] + step[1] * distance]);
    return true;
  }

  return {
    onPointerDown(pointer) {
      const point = worldPoint(pointer.world);
      if (point === null) return false;
      const target = contextTarget(context);
      const index = target === null ? null : hitVertex(target, point, pointer.scale);
      if (target !== null && index !== null) {
        const vertex = geometryVertices(target.geometry)[index] as Point2;
        dragStore.setState({
          drag: { entityId: target.id, index, start: point, point: vertex, kind: null },
        });
        return true;
      }
      const id = pointer.entityId;
      const state = context.store.getState();
      if (id === null || !editable(state.entities.byId[id], context.ui.getState().layerFlags)) {
        return false;
      }
      state.select([id]);
      return true;
    },
    onPointerMove(pointer) {
      const { drag } = dragStore.getState();
      const raw = worldPoint(pointer.world);
      if (drag === null || raw === null) return;
      const entity = context.store.getState().entities.byId[drag.entityId];
      if (!editable(entity, context.ui.getState().layerFlags)) {
        dragStore.setState({ drag: null });
        return;
      }
      const moved = apart(drag.start, raw, pointer.scale);
      const result = moved ? snapFor(entity, drag.index, raw, pointer.scale) : null;
      const point = result?.point ?? (geometryVertices(entity.geometry)[drag.index] as Point2);
      dragStore.setState({ drag: { ...drag, point, kind: result?.kind ?? null } });
    },
    onPointerUp(pointer) {
      const { drag } = dragStore.getState();
      if (drag === null) return;
      dragStore.setState({ drag: null });
      commit(drag, pointer);
    },
    onKeyDown(event) {
      if (event.key === "Escape") {
        if (dragStore.getState().drag !== null) {
          dragStore.setState({ drag: null });
          return true;
        }
        if (context.store.getState().selection.length === 0) return false;
        context.store.getState().clearSelection();
        return true;
      }
      return nudge(event);
    },
    preview: <DragPreview context={context} dragStore={dragStore} />,
    renderOverlay: (viewport) => (
      <VertexHandles context={context} dragStore={dragStore} viewport={viewport} />
    ),
  };
}

interface ToolViewProps {
  context: ToolContext;
  dragStore: StoreApi<DragState>;
}

/** Dashed geometry with the dragged vertex at its snapped position. */
function DragPreview({ context, dragStore }: ToolViewProps) {
  const drag = useStore(dragStore, (state) => state.drag);
  const entity = useStore(context.store, (state) =>
    drag === null ? undefined : state.entities.byId[drag.entityId],
  );
  if (drag === null || entity === undefined) return null;
  const geometry = withVertex(entity.geometry, drag.index, drag.point);
  const vertices = geometryVertices(geometry);
  const first = vertices[0];
  if (geometry.kind === "polyline" && geometry.closed && first !== undefined) vertices.push(first);
  return (
    <polyline
      data-testid="vertex-drag-preview"
      points={svgPoints(vertices)}
      fill="none"
      stroke="currentColor"
      strokeDasharray="4 4"
      strokeWidth={1}
      vectorEffect="non-scaling-stroke"
    />
  );
}

/** Keyboard-focusable handles of the target's vertices and the snap indicator. */
function VertexHandles({ context, dragStore, viewport }: ToolViewProps & { viewport: Viewport }) {
  const selection = useStore(context.store, (state) => state.selection);
  const byId = useStore(context.store, (state) => state.entities.byId);
  const flags = useStore(context.ui, (state) => state.layerFlags);
  const drag = useStore(dragStore, (state) => state.drag);
  const target = targetOf(selection, byId, flags);
  if (target === null) return null;
  const dragging = drag !== null && drag.entityId === target.id ? drag : null;
  const vertices = geometryVertices(target.geometry);
  if (dragging !== null) vertices[dragging.index] = dragging.point;
  const half = HANDLE_SIZE_PX / 2;
  const indicatorAt = dragging?.kind != null ? worldToScreen(viewport, dragging.point) : null;

  return (
    <div
      role="group"
      aria-label={VERTEX_TEXT.handles}
      style={{ position: "absolute", left: 0, top: 0, width: 0, height: 0 }}
    >
      {vertices.map((vertex, index) => {
        const [x, y] = worldToScreen(viewport, vertex);
        return (
          <button
            key={`${target.id}:${String(index)}`}
            type="button"
            data-vertex-entity={target.id}
            data-vertex-index={index}
            aria-label={VERTEX_TEXT.handle(index, vertices.length)}
            aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown"
            title={VERTEX_TEXT.hint}
            style={{
              position: "absolute",
              left: x - half,
              top: y - half,
              width: HANDLE_SIZE_PX,
              height: HANDLE_SIZE_PX,
              padding: 0,
              border: "1px solid #ff7a00",
              background: "#fff",
              pointerEvents: "none",
            }}
          />
        );
      })}
      {indicatorAt !== null && dragging?.kind != null && (
        <span
          role="status"
          data-testid="snap-indicator"
          style={{
            position: "absolute",
            left: indicatorAt[0] + HANDLE_SIZE_PX,
            top: indicatorAt[1] + HANDLE_SIZE_PX,
            whiteSpace: "nowrap",
            pointerEvents: "none",
          }}
        >
          {SNAP_TEXT.applied[dragging.kind]}
        </span>
      )}
    </div>
  );
}
