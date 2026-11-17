// Shared part of the triangulation and baseline-offset tools (spec 10 and 11.3, prototype screen
// 4): clicks on the canvas pick, in order, the point to fix (a point or symbol on a visible,
// unlocked layer) and its two references (points or symbols on visible layers, or vertices of lines
// and polylines, the wall corners of PoC decision D-15); a side panel next
// to the canvas shows them as chips, takes the tool's values (TriangulationDialog.tsx,
// BaselineOffsetDialog.tsx) and "Aplicar" runs the server operation (store/serverOps.ts), which
// flushes the queue, sends the PATCH and reloads the entities. The candidates computed on the
// client (geometry/preview.ts) are drawn on the canvas before applying; the server stays
// authoritative and its 422 messages are shown as they come. A point that already has a
// constraint gets the new one: the server replaces it without confirmation. Under the pointer a
// point or symbol wins over a line vertex; a vertex reference is sent as {entity_id, vertex}.
//
// Keyboard: the keyboard cursor clicks with Enter (CURSOR_TOOLS); the panel holds normal fields
// and buttons. Esc drops the picks.
import { type ReactNode } from "react";
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";
import { api as defaultApi } from "../../api/client";
import type { components } from "../../api/schema";
import { problemCode } from "../../features/plans/messages";
import { formatMetres, svgPoints, type CanvasPointer } from "../canvas/EditorCanvas";
import { layerFlags as flagsOf } from "../canvas/layers";
import {
  SERVER_OPERATION_TEXT,
  runServerOperation,
  type ServerOperationBody,
} from "../store/serverOps";
import { geometryVertices } from "../snapping/snap";
import type { EditorEntity, EntityId, Point2 } from "../store/types";
import type { EditorTool, ToolContext } from "../ui/editorUi";
import { HANDLE_HIT_PX, hitVertex } from "./VertexEditTool";

export const CONSTRAINT_TEXT = {
  slots: ["Punto a fijar", "Referencia A", "Referencia B"] as const,
  pick: (slot: string, reference: boolean) =>
    `Haz clic en el lienzo para elegir: ${slot} (${
      reference ? "un punto, un símbolo o una esquina de una línea" : "un punto o un símbolo"
    }).`,
  empty: "sin elegir",
  point: "Punto",
  symbol: "Símbolo",
  line: "Línea",
  polyline: "Polilínea",
  corner: (index: number, kind: string) => `Esquina ${String(index + 1)} de ${kind}`,
  cornerHint:
    "Las esquinas de una línea solo sirven como referencia si su longitud está fijada (herramienta Fijar longitud).",
  remove: (slot: string) => `Quitar ${slot.toLowerCase()}`,
  removeShort: "Quitar",
  apply: "Aplicar",
  cancel: "Cancelar",
  applying: "Aplicando…",
  noPlan: "No hay un plano abierto.",
  replaces: "Si el punto ya tiene una restricción, esta la reemplaza.",
} as const;

/** A picked point or symbol (`vertex` null) or a vertex of a line or polyline. */
export interface Pick {
  entityId: EntityId;
  vertex: number | null;
}

export type Picks = [Pick | null, Pick | null, Pick | null];

/** A reference as the PATCH takes it: a server id or a vertex of a line or polyline. */
export type ServerRef = string | components["schemas"]["VertexRef"];

export interface ConstraintState<V> {
  /** Point to fix, reference A, reference B. */
  picks: Picks;
  /** The tool's own values (typed text, chosen candidate or side). */
  values: V;
  busy: boolean;
  /** `hint` explains a V-07 when a reference is a line vertex. */
  notice: { text: string; error: boolean; hint?: string } | null;
}

/** What the client computes from the picked positions and the typed values. */
export interface ConstraintPreview {
  candidates: Point2[];
  /** Candidate that "Aplicar" would set; null when there is none. */
  selected: number | null;
  /** Candidate nearest to the current position of the point, when it matters. */
  nearest: number | null;
  /** Why there is no candidate (shown in the panel), or null. */
  problem: string | null;
}

export type Positions = [Point2, Point2, Point2];

export interface ConstraintConfig<V> {
  /** Panel title and accessible name. */
  title: string;
  /** Names of the point to fix and of the two references; CONSTRAINT_TEXT.slots by default. */
  slots?: readonly [string, string, string];
  initial: V;
  /** Preview, or null while a value is missing or invalid. */
  preview(positions: Positions, values: V): ConstraintPreview | null;
  /** PATCH body for the references' server ids, or the message of an invalid value. */
  request(
    refs: [ServerRef, ServerRef],
    values: V,
    preview: ConstraintPreview | null,
  ): ServerOperationBody | { error: string };
  /** 422 messages by `code`. */
  messages: Readonly<Record<string, string>>;
  done: string;
  /** Values chosen by a click on candidate `index`, or null when it does not choose. */
  choose?(values: V, index: number, preview: ConstraintPreview): V | null;
  /** Draw the line from reference A to B (the baseline). */
  drawBaseline?: boolean;
  /** The tool's fields. */
  Fields(props: FieldsProps<V>): ReactNode;
}

export interface FieldsProps<V> {
  values: V;
  setValues(values: Partial<V>): void;
  preview: ConstraintPreview | null;
  busy: boolean;
}

/** World position of a point or of a symbol's anchor; null for other kinds. */
export function positionOf(entity: EditorEntity | undefined): Point2 | null {
  if (entity === undefined || entity.deleted) return null;
  const { geometry } = entity;
  if (geometry.kind === "point") return geometry.coords_m[0] ?? null;
  if (geometry.kind === "symbol") return geometry.anchor_m;
  return null;
}

/** Point or symbol on a visible layer (and unlocked when `editable`). */
function pickable(context: ToolContext, entity: EditorEntity | undefined, editable: boolean) {
  if (positionOf(entity) === null || entity === undefined) return false;
  const layer = flagsOf(context.ui.getState().layerFlags, entity.layer);
  return layer.visible && !(editable && layer.locked);
}

/** Entity under the pointer, or the nearest pickable one within HANDLE_HIT_PX (keyboard cursor). */
export function hitPointEntity(
  context: ToolContext,
  pointer: CanvasPointer,
  editable: boolean,
): EntityId | null {
  const { byId, order } = context.store.getState().entities;
  if (pointer.entityId !== null && pickable(context, byId[pointer.entityId], editable)) {
    return pointer.entityId;
  }
  let best: { id: EntityId; px: number } | null = null;
  for (const id of order) {
    const entity = byId[id];
    const at = positionOf(entity);
    if (at === null || !pickable(context, entity, editable)) continue;
    const px = Math.hypot(at[0] - pointer.world[0], at[1] - pointer.world[1]) * pointer.scale;
    if (px <= HANDLE_HIT_PX && (best === null || px < best.px)) best = { id, px };
  }
  return best?.id ?? null;
}

function isLine(entity: EditorEntity | undefined): entity is EditorEntity {
  if (entity === undefined || entity.deleted) return false;
  return entity.geometry.kind === "line" || entity.geometry.kind === "polyline";
}

function visibleLine(
  context: ToolContext,
  entity: EditorEntity | undefined,
): entity is EditorEntity {
  return isLine(entity) && flagsOf(context.ui.getState().layerFlags, entity.layer).visible;
}

/**
 * Vertex of a line or polyline on a visible layer under the pointer: of the entity under it, or
 * else the nearest within HANDLE_HIT_PX of any of them (keyboard cursor, coincident corners).
 */
export function hitLineVertex(context: ToolContext, pointer: CanvasPointer): Pick | null {
  const { byId, order } = context.store.getState().entities;
  const under = pointer.entityId === null ? undefined : byId[pointer.entityId];
  if (visibleLine(context, under)) {
    const vertex = hitVertex(under, pointer.world, pointer.scale);
    if (vertex !== null) return { entityId: under.id, vertex };
  }
  let best: (Pick & { px: number }) | null = null;
  for (const id of order) {
    const entity = byId[id];
    if (!visibleLine(context, entity)) continue;
    const vertex = hitVertex(entity, pointer.world, pointer.scale);
    if (vertex === null) continue;
    const at = geometryVertices(entity.geometry)[vertex] as Point2;
    const px = Math.hypot(at[0] - pointer.world[0], at[1] - pointer.world[1]) * pointer.scale;
    if (best === null || px < best.px) best = { entityId: id, vertex, px };
  }
  return best === null ? null : { entityId: best.entityId, vertex: best.vertex };
}

/** World position of a pick: the point, the symbol's anchor or the line vertex. */
export function pickPosition(context: ToolContext, pick: Pick | null): Point2 | null {
  if (pick === null) return null;
  const entity = context.store.getState().entities.byId[pick.entityId];
  if (pick.vertex === null) return positionOf(entity);
  if (!isLine(entity)) return null;
  return geometryVertices(entity.geometry)[pick.vertex] ?? null;
}

function samePick(a: Pick | null, b: Pick): boolean {
  return a !== null && a.entityId === b.entityId && a.vertex === b.vertex;
}

function positionsOf(context: ToolContext, picks: Picks): Positions | null {
  const positions = picks.map((pick) => pickPosition(context, pick));
  return positions.every((p) => p !== null) ? (positions as Positions) : null;
}

function computePreview<V>(
  context: ToolContext,
  config: ConstraintConfig<V>,
  state: ConstraintState<V>,
): ConstraintPreview | null {
  const positions = positionsOf(context, state.picks);
  return positions === null ? null : config.preview(positions, state.values);
}

export function createConstraintTool<V>(
  context: ToolContext,
  config: ConstraintConfig<V>,
): EditorTool {
  const tool = createStore<ConstraintState<V>>()(() => ({
    picks: [null, null, null],
    values: config.initial,
    busy: false,
    notice: null,
  }));
  const api = context.api ?? defaultApi;

  function clear(): void {
    if (!tool.getState().busy) tool.setState({ picks: [null, null, null], notice: null });
  }

  async function apply(): Promise<void> {
    const state = tool.getState();
    const [target, a, b] = state.picks;
    if (state.busy || target === null || a === null || b === null) return;
    const revisionId = context.store.getState().revisionId;
    if (revisionId === null || revisionId === "") {
      tool.setState({ notice: { text: CONSTRAINT_TEXT.noPlan, error: true } });
      return;
    }
    const preview = computePreview(context, config, state);
    // Check the values before sending anything (V-04, V-06 on the client).
    const checked = config.request(["", ""], state.values, preview);
    if ("error" in checked) {
      tool.setState({ notice: { text: checked.error, error: true } });
      return;
    }
    tool.setState({ busy: true, notice: null });
    const result = await runServerOperation(
      api,
      context.store,
      { revisionId },
      target.entityId,
      (serverId) => {
        const ref = (pick: Pick): ServerRef =>
          pick.vertex === null
            ? serverId(pick.entityId)
            : { entity_id: serverId(pick.entityId), vertex: pick.vertex };
        // Already checked above, so this is a body.
        return config.request([ref(a), ref(b)], state.values, preview) as ServerOperationBody;
      },
      config.messages,
    );
    if (result.ok) {
      tool.setState({
        picks: [null, null, null],
        busy: false,
        notice: { text: config.done, error: false },
      });
      return;
    }
    const corner = a.vertex !== null || b.vertex !== null;
    const hint =
      corner && problemCode(result.error) === "V-07" ? CONSTRAINT_TEXT.cornerHint : undefined;
    tool.setState({
      busy: false,
      notice: { text: result.message, error: true, ...(hint === undefined ? {} : { hint }) },
    });
  }

  return {
    onPointerDown(pointer) {
      const state = tool.getState();
      if (state.busy) return true;
      const preview = computePreview(context, config, state);
      if (preview !== null && config.choose !== undefined) {
        const [x, y] = pointer.world;
        const index = preview.candidates.findIndex(
          (c) => Math.hypot(c[0] - x, c[1] - y) * pointer.scale <= HANDLE_HIT_PX,
        );
        const values = index === -1 ? null : config.choose(state.values, index, preview);
        if (values !== null) {
          tool.setState({ values, notice: null });
          return true;
        }
      }
      const slot = state.picks.indexOf(null);
      if (slot === -1) return true;
      // The point to fix is a point or symbol; a reference may also be a line vertex.
      const id = hitPointEntity(context, pointer, slot === 0);
      let pick: Pick | null = id === null ? null : { entityId: id, vertex: null };
      if (pick === null && slot !== 0) pick = hitLineVertex(context, pointer);
      if (pick === null || state.picks.some((other) => samePick(other, pick))) return true;
      const picks = [...state.picks] as Picks;
      picks[slot] = pick;
      tool.setState({ picks, notice: null });
      return true;
    },
    onKeyDown(event) {
      if (event.key !== "Escape" || tool.getState().picks.every((p) => p === null)) return false;
      clear();
      return true;
    },
    preview: <Markers context={context} config={config} tool={tool} />,
    panel: <Panel context={context} config={config} tool={tool} apply={apply} clear={clear} />,
  };
}

interface ViewProps<V> {
  context: ToolContext;
  config: ConstraintConfig<V>;
  tool: StoreApi<ConstraintState<V>>;
}

/** Live picks, positions and preview; re-renders on entity and layer changes. */
function useConstraint<V>({ context, config, tool }: ViewProps<V>) {
  const state = useStore(tool);
  useStore(context.store, (s) => s.entities);
  useStore(context.ui, (s) => s.layerFlags);
  return {
    state,
    positions: state.picks.map((pick) => pickPosition(context, pick)),
    preview: computePreview(context, config, state),
  };
}

function dot(point: Point2, color: string, size: number, extra: Record<string, string> = {}) {
  return (
    <path
      {...extra}
      d={`M${svgPoints([point])}h0`}
      stroke={color}
      strokeWidth={size}
      strokeLinecap="round"
      vectorEffect="non-scaling-stroke"
    />
  );
}

function Markers<V>(props: ViewProps<V>) {
  const { positions, preview } = useConstraint(props);
  const [, a, b] = positions;
  return (
    <g fill="none">
      {props.config.drawBaseline === true && a != null && b != null && (
        <polyline
          data-testid="constraint-baseline"
          points={svgPoints([a, b])}
          stroke="#ff7a00"
          strokeWidth={1.5}
          strokeDasharray="6 3"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {positions.map(
        (p, i) =>
          p != null && (
            <g key={`pick-${String(i)}`}>
              {dot(p, "#ff7a00", 12, { "data-testid": "constraint-pick" })}
            </g>
          ),
      )}
      {preview?.candidates.map((c, i) => (
        <g key={`candidate-${String(i)}`}>
          {dot(c, i === preview.selected ? "#d0006f" : "#8a99a8", i === preview.selected ? 12 : 9, {
            "data-testid": "constraint-candidate",
            "data-selected": String(i === preview.selected),
            "data-nearest": String(i === preview.nearest),
          })}
        </g>
      ))}
    </g>
  );
}

function pickLabel(context: ToolContext, pick: Pick): string {
  const entity = context.store.getState().entities.byId[pick.entityId];
  const at = pickPosition(context, pick);
  if (entity === undefined || at === null) return CONSTRAINT_TEXT.empty;
  const where = `x ${formatMetres(at[0])} · y ${formatMetres(at[1])}`;
  if (pick.vertex !== null) {
    const kind =
      entity.geometry.kind === "polyline" ? CONSTRAINT_TEXT.polyline : CONSTRAINT_TEXT.line;
    return `${CONSTRAINT_TEXT.corner(pick.vertex, kind)} (${where})`;
  }
  const kind = entity.geometry.kind === "symbol" ? CONSTRAINT_TEXT.symbol : CONSTRAINT_TEXT.point;
  return `${kind} en ${where}`;
}

function Panel<V>(props: ViewProps<V> & { apply(): Promise<void>; clear(): void }) {
  const { context, config, tool, apply, clear } = props;
  const { state, preview } = useConstraint(props);
  const next = state.picks.indexOf(null);
  const { Fields, slots = CONSTRAINT_TEXT.slots } = config;
  return (
    <aside
      className="editor-constraint-panel"
      aria-label={config.title}
      style={{ width: 280, padding: 8 }}
    >
      <h2 style={{ fontSize: 16, margin: "0 0 8px" }}>{config.title}</h2>
      <ul style={{ listStyle: "none", padding: 0, margin: "0 0 8px" }}>
        {slots.map((slot, i) => {
          const pick = state.picks[i] ?? null;
          return (
            <li key={slot} data-testid="constraint-chip">
              {slot}: {pick === null ? CONSTRAINT_TEXT.empty : pickLabel(context, pick)}{" "}
              {pick !== null && (
                <button
                  type="button"
                  aria-label={CONSTRAINT_TEXT.remove(slot)}
                  disabled={state.busy}
                  onClick={() => {
                    const picks = [...tool.getState().picks] as Picks;
                    picks[i] = null;
                    tool.setState({ picks, notice: null });
                  }}
                >
                  {CONSTRAINT_TEXT.removeShort}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {next !== -1 && (
        <p role="status" style={{ margin: "0 0 8px" }}>
          {CONSTRAINT_TEXT.pick(slots[next] ?? "", next !== 0)}
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void apply();
        }}
      >
        <Fields
          values={state.values}
          setValues={(values) =>
            // A new value makes the message of the previous attempt stale.
            tool.setState({ values: { ...tool.getState().values, ...values }, notice: null })
          }
          preview={preview}
          busy={state.busy}
        />
        {preview?.problem != null && state.notice === null && <p role="alert">{preview.problem}</p>}
        {state.notice !== null && (
          <p role={state.notice.error ? "alert" : "status"}>{state.notice.text}</p>
        )}
        {state.notice?.hint !== undefined && <p>{state.notice.hint}</p>}
        <button type="submit" disabled={state.busy || next !== -1}>
          {state.busy ? CONSTRAINT_TEXT.applying : CONSTRAINT_TEXT.apply}
        </button>
        <button type="button" disabled={state.busy} onClick={clear}>
          {CONSTRAINT_TEXT.cancel}
        </button>
        <p style={{ fontSize: 12 }}>{CONSTRAINT_TEXT.replaces}</p>
        <p style={{ fontSize: 12 }}>{SERVER_OPERATION_TEXT.noUndo}</p>
      </form>
    </aside>
  );
}
