// Fix-length tool (spec 10 and 11.3, US-07, REQ-06, V-04): a click on a segment of a line or
// polyline picks it and marks its two ends; a click on one end makes it the anchor (it stays) and
// the other end free (it moves, and with it the rest of the polyline on its side). An inline field
// then takes the declared length ("4,20" or "4.20"); it is checked here first (V-04) and nothing
// is sent when it is invalid. "Aplicar" runs the server's `fix_length` (store/serverOps.ts), which
// also reloads the entities, so Deshacer does not revert it. Esc cancels.
//
// Keyboard: the keyboard cursor clicks with Enter like in the drawing tools; the ends are buttons
// laid over the canvas (Tab reaches them, Enter picks the anchor) and the field is a normal input.
// Entities on hidden or locked layers are not picked.
import { useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";
import { api as defaultApi } from "../../api/client";
import { formatMetres, svgPoints, type CanvasPointer } from "../canvas/EditorCanvas";
import { layerFlags as flagsOf, type LayerFlagsByName } from "../canvas/layers";
import { worldToScreen, type Viewport } from "../canvas/viewport";
import { geometryVertices } from "../snapping/snap";
import { SERVER_OPERATION_TEXT, runServerOperation } from "../store/serverOps";
import type { EditorEntity, EntityId, Point2 } from "../store/types";
import type { EditorTool, ToolContext } from "../ui/editorUi";
import { DISTANCE_MESSAGE, parseDistance } from "../util/parseDistance";
import { HANDLE_HIT_PX, HANDLE_SIZE_PX } from "./VertexEditTool";

export const FIX_LENGTH_TEXT = {
  pickSegment: "Fijar longitud: haz clic en un segmento de una línea o polilínea.",
  pickAnchor: "Elige el vértice ancla (queda fijo); el otro extremo se mueve.",
  ends: "Extremos del segmento",
  end: (n: number) => `Extremo ${String(n)}: elegir como ancla`,
  anchor: "ancla",
  free: "libre",
  endRole: (n: number, role: string) => `Extremo ${String(n)}, ${role}`,
  current: (length: number) => `Longitud actual: ${formatMetres(length)}`,
  field: "Longitud del segmento (m)",
  hint: "Coma o punto decimal, por ejemplo 4,20",
  apply: "Aplicar",
  cancel: "Cancelar",
  applying: "Aplicando…",
  noPlan: "No hay un plano abierto.",
  done: (value: number) => `Longitud fijada en ${formatMetres(value)}`,
} as const;

/** The picked segment: its entity and the indices of its two vertices, in drawing order. */
export interface PickedSegment {
  entityId: EntityId;
  ends: [number, number];
}

interface FixState {
  picked: PickedSegment | null;
  /** Vertex index of the anchor, one of `picked.ends`. */
  anchor: number | null;
  busy: boolean;
  notice: { text: string; error: boolean } | null;
}

const IDLE = { picked: null, anchor: null, busy: false } as const;

function fixable(entity: EditorEntity | undefined, flags: LayerFlagsByName): entity is EditorEntity {
  if (entity === undefined || entity.deleted) return false;
  if (entity.geometry.kind !== "line" && entity.geometry.kind !== "polyline") return false;
  const layer = flagsOf(flags, entity.layer);
  return layer.visible && !layer.locked;
}

/** Vertex index pairs of the segments of a line or polyline (closing segment included). */
export function segmentsOf(entity: EditorEntity): [number, number][] {
  const n = geometryVertices(entity.geometry).length;
  const segments: [number, number][] = [];
  for (let i = 0; i + 1 < n; i += 1) segments.push([i, i + 1]);
  const closed = entity.geometry.kind === "polyline" && entity.geometry.closed;
  if (closed && n > 2) segments.push([n - 1, 0]);
  return segments;
}

function distanceToSegment(p: Point2, a: Point2, b: Point2): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const length2 = dx * dx + dy * dy;
  const along = length2 === 0 ? 0 : ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2;
  const t = Math.max(0, Math.min(1, along));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

interface SegmentHit {
  ends: [number, number];
  /** Distance from the point, in metres. */
  m: number;
}

function nearestSegment(entity: EditorEntity, world: Point2): SegmentHit | null {
  const vertices = geometryVertices(entity.geometry);
  let best: SegmentHit | null = null;
  for (const ends of segmentsOf(entity)) {
    const m = distanceToSegment(world, vertices[ends[0]] as Point2, vertices[ends[1]] as Point2);
    if (best === null || m < best.m) best = { ends, m };
  }
  return best;
}

/**
 * Segment under `pointer`: the nearest segment of the entity under it, or else of any fixable
 * entity within HANDLE_HIT_PX (the keyboard cursor reports no entity).
 */
export function hitSegment(context: ToolContext, pointer: CanvasPointer): PickedSegment | null {
  const { byId, order } = context.store.getState().entities;
  const flags = context.ui.getState().layerFlags;
  const under = pointer.entityId === null ? undefined : byId[pointer.entityId];
  if (fixable(under, flags)) {
    const hit = nearestSegment(under, pointer.world);
    if (hit !== null) return { entityId: under.id, ends: hit.ends };
  }
  let best: (PickedSegment & { m: number }) | null = null;
  for (const id of order) {
    const entity = byId[id];
    if (!fixable(entity, flags)) continue;
    const hit = nearestSegment(entity, pointer.world);
    if (hit === null || hit.m * pointer.scale > HANDLE_HIT_PX) continue;
    if (best === null || hit.m < best.m) {
      best = { entityId: id, ends: hit.ends, m: hit.m };
    }
  }
  return best === null ? null : { entityId: best.entityId, ends: best.ends };
}

function endPoints(context: ToolContext, picked: PickedSegment): [Point2, Point2] | null {
  const entity = context.store.getState().entities.byId[picked.entityId];
  if (!fixable(entity, context.ui.getState().layerFlags)) return null;
  const vertices = geometryVertices(entity.geometry);
  const a = vertices[picked.ends[0]];
  const b = vertices[picked.ends[1]];
  return a === undefined || b === undefined ? null : [a, b];
}

export function createFixLengthTool(context: ToolContext): EditorTool {
  const fix = createStore<FixState>()(() => ({ ...IDLE, notice: null }));
  const api = context.api ?? defaultApi;

  function cancel(): void {
    if (!fix.getState().busy) fix.setState({ ...IDLE });
  }

  async function apply(text: string): Promise<void> {
    const { picked, anchor, busy } = fix.getState();
    if (picked === null || anchor === null || busy) return;
    const value = parseDistance(text);
    if (value === null) {
      fix.setState({ notice: { text: DISTANCE_MESSAGE, error: true } });
      return;
    }
    const revisionId = context.store.getState().revisionId;
    if (revisionId === null || revisionId === "") {
      fix.setState({ notice: { text: FIX_LENGTH_TEXT.noPlan, error: true } });
      return;
    }
    const free = picked.ends[0] === anchor ? picked.ends[1] : picked.ends[0];
    fix.setState({ busy: true, notice: null });
    const result = await runServerOperation(api, context.store, { revisionId }, picked.entityId, {
      operation: "fix_length",
      anchor_vertex: anchor,
      free_vertex: free,
      value_m: value,
    });
    fix.setState(
      result.ok
        ? { ...IDLE, notice: { text: FIX_LENGTH_TEXT.done(value), error: false } }
        : { busy: false, notice: { text: result.message, error: true } },
    );
  }

  return {
    onPointerDown(pointer) {
      const state = fix.getState();
      if (state.busy) return true;
      const ends = state.picked === null ? null : endPoints(context, state.picked);
      if (state.picked !== null && ends !== null) {
        const [x, y] = pointer.world;
        const index = ends.findIndex(
          (end) => Math.hypot(end[0] - x, end[1] - y) * pointer.scale <= HANDLE_HIT_PX,
        );
        if (index !== -1) {
          fix.setState({ anchor: state.picked.ends[index] ?? null, notice: null });
          return true;
        }
      }
      const picked = hitSegment(context, pointer);
      if (picked !== null) fix.setState({ picked, anchor: null, notice: null });
      return true;
    },
    onKeyDown(event) {
      if (event.key !== "Escape" || fix.getState().picked === null) return false;
      cancel();
      return true;
    },
    preview: <SegmentPreview context={context} fix={fix} />,
    renderOverlay: (viewport) => (
      <FixOverlay context={context} fix={fix} viewport={viewport} apply={apply} cancel={cancel} />
    ),
  };
}

interface ViewProps {
  context: ToolContext;
  fix: StoreApi<FixState>;
}

/** Live end points of the picked segment, or null. */
function usePickedEnds({ context, fix }: ViewProps): [Point2, Point2] | null {
  const picked = useStore(fix, (state) => state.picked);
  // Re-render when the entity or the layer flags change.
  useStore(context.store, (state) =>
    picked === null ? undefined : state.entities.byId[picked.entityId],
  );
  useStore(context.ui, (state) => state.layerFlags);
  return picked === null ? null : endPoints(context, picked);
}

function SegmentPreview(props: ViewProps) {
  const ends = usePickedEnds(props);
  if (ends === null) return null;
  return (
    <polyline
      data-testid="fix-length-segment"
      points={svgPoints(ends)}
      fill="none"
      stroke="#ff7a00"
      strokeWidth={4}
      vectorEffect="non-scaling-stroke"
    />
  );
}

function FixOverlay({
  context,
  fix,
  viewport,
  apply,
  cancel,
}: ViewProps & { viewport: Viewport; apply(text: string): Promise<void>; cancel(): void }) {
  const ends = usePickedEnds({ context, fix });
  const { picked, anchor, busy, notice } = useStore(fix);
  let hint: string | null = null;
  if (picked === null || ends === null) hint = FIX_LENGTH_TEXT.pickSegment;
  else if (anchor === null) hint = FIX_LENGTH_TEXT.pickAnchor;
  const half = HANDLE_SIZE_PX / 2;
  return (
    <div style={{ position: "absolute", left: 0, top: 0, width: 0, height: 0 }}>
      <div style={{ position: "absolute", left: 8, top: 8, width: 360, pointerEvents: "none" }}>
        {hint !== null && <p role="status" style={{ margin: 0 }}>{hint}</p>}
        {notice !== null && (
          <p role={notice.error ? "alert" : "status"} style={{ margin: 0 }}>
            {notice.text}
          </p>
        )}
      </div>
      {picked !== null && ends !== null && (
        <div role="group" aria-label={FIX_LENGTH_TEXT.ends}>
          {ends.map((end, i) => {
            const [x, y] = worldToScreen(viewport, end);
            const vertex = picked.ends[i] as number;
            const isAnchor = vertex === anchor;
            let role: string | null = null;
            if (anchor !== null) role = isAnchor ? FIX_LENGTH_TEXT.anchor : FIX_LENGTH_TEXT.free;
            return (
              <button
                key={vertex}
                type="button"
                aria-label={
                  role === null ? FIX_LENGTH_TEXT.end(i + 1) : FIX_LENGTH_TEXT.endRole(i + 1, role)
                }
                aria-pressed={isAnchor}
                disabled={busy}
                onClick={() => fix.setState({ anchor: vertex, notice: null })}
                style={{
                  position: "absolute",
                  left: x - half,
                  top: y - half,
                  minWidth: HANDLE_SIZE_PX,
                  height: HANDLE_SIZE_PX,
                  padding: 0,
                  fontSize: 10,
                  lineHeight: 1,
                  border: "1px solid #ff7a00",
                  background: isAnchor ? "#ff7a00" : "#fff",
                  pointerEvents: "none",
                }}
              >
                {role}
              </button>
            );
          })}
        </div>
      )}
      {picked !== null && ends !== null && anchor !== null && (
        <LengthForm
          key={`${picked.entityId}:${String(anchor)}`}
          at={worldToScreen(viewport, midpoint(ends[0], ends[1]))}
          length={Math.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1])}
          busy={busy}
          apply={apply}
          cancel={cancel}
        />
      )}
    </div>
  );
}

function midpoint(a: Point2, b: Point2): Point2 {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}

/** The editor canvas around `element`, to give it the focus back. */
function canvasOf(element: Element | null): SVGSVGElement | null {
  return (
    element
      ?.closest(".editor-canvas-viewport")
      ?.querySelector<SVGSVGElement>("[data-testid='editor-canvas']") ?? null
  );
}

function LengthForm({
  at,
  length,
  busy,
  apply,
  cancel,
}: {
  at: Point2;
  length: number;
  busy: boolean;
  apply(text: string): Promise<void>;
  cancel(): void;
}) {
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
    // The press that picked the anchor focuses the canvas after this mounts; take focus back.
    const timer = setTimeout(() => inputRef.current?.focus());
    return () => clearTimeout(timer);
  }, []);
  return (
    <form
      className="editor-fix-length"
      style={{ position: "absolute", left: at[0] + 12, top: at[1] + 12, background: "#fff" }}
      onSubmit={(event) => {
        event.preventDefault();
        const canvas = canvasOf(inputRef.current);
        // Once applied the form goes away: the keyboard user goes on from the canvas.
        void apply(text).then(() => {
          if (inputRef.current === null) canvas?.focus();
        });
      }}
    >
      <p style={{ margin: 0 }}>{FIX_LENGTH_TEXT.current(length)}</p>
      <input
        ref={inputRef}
        type="text"
        inputMode="decimal"
        aria-label={FIX_LENGTH_TEXT.field}
        title={FIX_LENGTH_TEXT.hint}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          canvasOf(inputRef.current)?.focus();
          cancel();
        }}
      />
      <button type="submit" disabled={busy}>
        {busy ? FIX_LENGTH_TEXT.applying : FIX_LENGTH_TEXT.apply}
      </button>
      <button type="button" disabled={busy} onClick={cancel}>
        {FIX_LENGTH_TEXT.cancel}
      </button>
      <p style={{ margin: 0, fontSize: 12 }}>{SERVER_OPERATION_TEXT.noUndo}</p>
    </form>
  );
}
