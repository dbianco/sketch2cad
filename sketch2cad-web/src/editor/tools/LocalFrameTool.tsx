// Local frame tool (US-05, REQ-04, spec 11.1): a press on the canvas sets the new origin and a drag
// from it the +X direction, with a live preview line; pressing on the pending (or current) origin
// and dragging changes only the direction. The panel shows the current frame, applies the pending
// origin and axis, and sets the North angle with a numeric field. Clicks are snapped like drawing.
//
// The canvas shows local coordinates of the CURRENT frame, so a click becomes paper mm through
// frame.ts with the plan's paper scale (provisional 1:100 when none), exactly as the API computes.
//
// An origin or axis change makes the server rewrite every coordinate of the draft revision. So the
// tool first takes the edit lease if it does not hold it, flushes the operation queue (ops still
// queued would be in the OLD frame; if the queue does not drain the change is aborted), sends
// PUT local-frame and reloads the revision's entities into the editor store. A North-only change is
// one PUT and no reload. A PUT answered with `lease_required` takes the lease and is retried once.
// While this tool is active no other tool can edit, so nothing is queued during a change.
import { useState } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { ApiError, api as defaultApi, type ApiClient } from "../../api/client";
import { v12Message } from "../../features/calibration/messages";
import { svgPoints, type CanvasPointer } from "../canvas/EditorCanvas";
import {
  DEFAULT_FRAME,
  directionDeg,
  localToPaper,
  normalizeDeg,
  samePlacement,
  worldAxisAngle,
  type LocalFrame,
} from "../frame/frame";
import {
  holdsLease,
  planPaperScale,
  planStore,
  type PlanOut,
  type PlanState,
  type PlanStore,
} from "../plan/planStore";
import type { EditorStore } from "../store/entities";
import { flushQueue, type FlushResult } from "../store/sync";
import type { Point2 } from "../store/types";
import type { EditorTool, EditorUiStore, ToolContext } from "../ui/editorUi";
import { apart, snappedDrawPoint } from "./LineTool";
import { HANDLE_HIT_PX } from "./VertexEditTool";

export const LOCAL_FRAME_TEXT = {
  panel: "Sistema local",
  current: "Sistema actual",
  none: "Sin sistema local: origen del papel y ejes del dibujo.",
  origin: "Origen en el papel",
  axis: "Eje +X",
  north: "Norte",
  scale: "Escala",
  provisional: "provisional",
  hint: "Haz clic en el lienzo para fijar el origen y arrastra desde él para fijar el eje +X.",
  pending: "Nuevo sistema",
  applyPlacement: "Aplicar origen y eje +X",
  discard: "Descartar",
  northField: "Ángulo del Norte (grados)",
  applyNorth: "Aplicar Norte",
  working: "Aplicando…",
  done: "Sistema local actualizado.",
  northDone: "Norte actualizado.",
  unchanged: "No hay cambios que aplicar.",
  invalidNorth: "Indica el ángulo del Norte en grados.",
  noPlan: "No hay ningún plano abierto.",
  noCalibration: "Acepta una calibración antes de fijar el sistema local.",
  busy: "Aún se están enviando cambios. Espera un momento e inténtalo de nuevo.",
  flushFailed:
    "No se pudieron guardar los cambios pendientes, así que el sistema local no se ha cambiado. " +
    "Revisa la conexión y el bloqueo de edición e inténtalo de nuevo.",
  leaseRequired:
    "Necesitas el bloqueo de edición del plano para cambiar el sistema local. Inténtalo de nuevo.",
  validated: "Este plano está validado. Reábrelo para editarlo.",
  notEditable:
    "El plano no admite este cambio ahora: necesita una calibración aceptada y una revisión en borrador.",
  forbidden: "No tienes permiso para editar este plano.",
  reloadFailed:
    "El sistema local se cambió, pero no se pudieron recargar los elementos. Recarga la página " +
    "antes de seguir editando.",
  failed: "No se pudo cambiar el sistema local. Inténtalo de nuevo.",
  otherUser: "Otro usuario",
} as const;

type FrameApi = Pick<ApiClient, "get" | "post" | "put" | "patch" | "delete">;

/** What applying a frame needs. */
export interface FrameDeps {
  api: FrameApi;
  store: StoreApi<EditorStore>;
  plan: StoreApi<PlanStore>;
}

export type FrameResult = { ok: true; reloaded: boolean } | { ok: false; message: string };

/** The stored frame, or the default frame the server uses before one is set. */
export function currentFrame(state: Pick<PlanState, "localFrame">): LocalFrame {
  return state.localFrame ?? DEFAULT_FRAME;
}

/**
 * Frame the tool's draft describes: `draft[0]` is the new origin and `draft[1]` a point on the new
 * +X axis, both in local coordinates of the current frame; `null` without a draft. North is kept.
 */
export function pendingFrame(
  state: Pick<PlanState, "localFrame" | "paperScale">,
  draft: readonly Point2[],
): LocalFrame | null {
  const [origin, axisPoint] = draft;
  if (origin === undefined) return null;
  const frame = currentFrame(state);
  // The local origin is the current origin: keep its stored value, free of round-off.
  const atOrigin = origin[0] === 0 && origin[1] === 0;
  return {
    origin_paper_mm: atOrigin
      ? frame.origin_paper_mm
      : localToPaper(frame, planPaperScale(state), origin),
    x_axis_angle_deg:
      axisPoint === undefined
        ? frame.x_axis_angle_deg
        : worldAxisAngle(frame, directionDeg(origin, axisPoint)),
    north_angle_deg: frame.north_angle_deg,
  };
}

function problemCode(error: ApiError): string | undefined {
  const code = error.problem.code;
  return typeof code === "string" ? code : undefined;
}

function holderOf(error: ApiError): PlanState["lease"] {
  const holder = error.problem.holder;
  if (holder === null || typeof holder !== "object") return null;
  const { user_id, display_name, expires_at } = holder as Record<string, unknown>;
  return typeof user_id === "string" &&
    typeof display_name === "string" &&
    typeof expires_at === "string"
    ? { user_id, display_name, expires_at }
    : null;
}

/** Spanish message for a failed lease, flush or frame request. */
export function frameErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return LOCAL_FRAME_TEXT.failed;
  if (error.status === 403) return LOCAL_FRAME_TEXT.forbidden;
  if (error.status !== 409) return LOCAL_FRAME_TEXT.failed;
  switch (problemCode(error)) {
    case "V-12":
      return v12Message(holderOf(error)?.display_name ?? LOCAL_FRAME_TEXT.otherUser);
    case "V-10":
      return LOCAL_FRAME_TEXT.validated;
    case "lease_required":
      return LOCAL_FRAME_TEXT.leaseRequired;
    default:
      return LOCAL_FRAME_TEXT.notEditable;
  }
}

function flushMessage(result: Exclude<FlushResult, { status: "drained" }>): string {
  if (result.status === "busy") return LOCAL_FRAME_TEXT.busy;
  if (result.status === "conflict") {
    const code = problemCode(result.error);
    if (code === "V-12" || code === "V-10") return frameErrorMessage(result.error);
  }
  return LOCAL_FRAME_TEXT.flushFailed;
}

async function acquireLease(deps: FrameDeps, planId: string): Promise<void> {
  try {
    const lease = await deps.api.post("/api/v1/plans/{plan_id}/lease", {
      path: { plan_id: planId },
    });
    deps.plan.getState().acquiredLease(lease);
  } catch (error) {
    if (error instanceof ApiError && problemCode(error) === "V-12") {
      deps.plan.getState().setLease(holderOf(error));
    }
    throw error;
  }
}

async function putFrame(deps: FrameDeps, planId: string, frame: LocalFrame): Promise<PlanOut> {
  const send = () =>
    deps.api.put("/api/v1/plans/{plan_id}/local-frame", {
      path: { plan_id: planId },
      body: frame,
    });
  try {
    return await send();
  } catch (error) {
    if (!(error instanceof ApiError) || problemCode(error) !== "lease_required") throw error;
    await acquireLease(deps, planId);
    return send();
  }
}

/**
 * Store `frame` on the plan. A new origin or axis flushes the queue first (taking the lease if
 * needed) and reloads the entities the server moved; a North-only change is a single PUT.
 */
export async function applyFrame(deps: FrameDeps, frame: LocalFrame): Promise<FrameResult> {
  const state = deps.plan.getState();
  const { planId, revisionId } = state;
  if (planId === null || revisionId === null) return { ok: false, message: LOCAL_FRAME_TEXT.noPlan };
  if (!state.calibrated) return { ok: false, message: LOCAL_FRAME_TEXT.noCalibration };
  const reload = !samePlacement(currentFrame(state), frame);
  let updated: PlanOut;
  try {
    if (reload) {
      if (!holdsLease(state)) await acquireLease(deps, planId);
      const flushed = await flushQueue(deps.api, deps.store, { revisionId });
      if (flushed.status !== "drained") return { ok: false, message: flushMessage(flushed) };
    }
    updated = await putFrame(deps, planId, frame);
  } catch (error) {
    return { ok: false, message: frameErrorMessage(error) };
  }
  deps.plan.getState().setPlan(updated);
  if (!reload) return { ok: true, reloaded: false };
  const reloadId = updated.current_revision_id ?? revisionId;
  try {
    const { entities } = await deps.api.get("/api/v1/revisions/{revision_id}/entities", {
      path: { revision_id: reloadId },
    });
    deps.store.getState().load(reloadId, entities);
  } catch {
    return { ok: false, message: LOCAL_FRAME_TEXT.reloadFailed };
  }
  return { ok: true, reloaded: true };
}

const numberFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 2 });

function formatDeg(value: number): string {
  return `${numberFormat.format(value)}°`;
}

function formatPaper(point: Point2): string {
  return `${numberFormat.format(point[0])}; ${numberFormat.format(point[1])} mm`;
}

function FrameSummary({ frame }: { frame: LocalFrame }) {
  return (
    <>
      <dt>{LOCAL_FRAME_TEXT.origin}</dt>
      <dd>{formatPaper(frame.origin_paper_mm)}</dd>
      <dt>{LOCAL_FRAME_TEXT.axis}</dt>
      <dd>{formatDeg(frame.x_axis_angle_deg)}</dd>
    </>
  );
}

export interface LocalFramePanelProps {
  ui: StoreApi<EditorUiStore>;
  store: StoreApi<EditorStore>;
  plan?: StoreApi<PlanStore>;
  api?: FrameApi;
}

export function LocalFramePanel({
  ui,
  store,
  plan = planStore,
  api = defaultApi,
}: LocalFramePanelProps) {
  const localFrame = useStore(plan, (state) => state.localFrame);
  const paperScale = useStore(plan, (state) => state.paperScale);
  const draft = useStore(ui, (state) => state.draft);
  const [northText, setNorthText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  const frame = localFrame ?? DEFAULT_FRAME;
  const pending = pendingFrame({ localFrame, paperScale }, draft);
  const placementChanged = pending !== null && !samePlacement(frame, pending);
  const northValue = northText ?? String(frame.north_angle_deg);

  async function run(next: LocalFrame, success: string) {
    setBusy(true);
    setMessage(null);
    const result = await applyFrame({ api, store, plan }, next);
    setBusy(false);
    if (!result.ok) {
      setMessage({ text: result.message, error: true });
      return;
    }
    if (result.reloaded) ui.getState().cancelDraft();
    setNorthText(null);
    setMessage({ text: success, error: false });
  }

  function onApplyNorth() {
    const value = Number(northValue);
    if (northValue.trim() === "" || !Number.isFinite(value)) {
      setMessage({ text: LOCAL_FRAME_TEXT.invalidNorth, error: true });
      return;
    }
    const north = normalizeDeg(value);
    if (north === frame.north_angle_deg) {
      setMessage({ text: LOCAL_FRAME_TEXT.unchanged, error: false });
      return;
    }
    void run({ ...frame, north_angle_deg: north }, LOCAL_FRAME_TEXT.northDone);
  }

  return (
    <aside className="editor-local-frame" aria-label={LOCAL_FRAME_TEXT.panel}>
      <p>{LOCAL_FRAME_TEXT.current}</p>
      {localFrame === null && <p>{LOCAL_FRAME_TEXT.none}</p>}
      <dl data-testid="local-frame-current">
        <FrameSummary frame={frame} />
        <dt>{LOCAL_FRAME_TEXT.north}</dt>
        <dd>{formatDeg(frame.north_angle_deg)}</dd>
        <dt>{LOCAL_FRAME_TEXT.scale}</dt>
        <dd>
          {`1:${String(planPaperScale({ paperScale }))}`}
          {paperScale === null && ` (${LOCAL_FRAME_TEXT.provisional})`}
        </dd>
      </dl>
      {pending === null ? (
        <p>{LOCAL_FRAME_TEXT.hint}</p>
      ) : (
        <>
          <p>{LOCAL_FRAME_TEXT.pending}</p>
          <dl data-testid="local-frame-pending">
            <FrameSummary frame={pending} />
          </dl>
        </>
      )}
      <button
        type="button"
        disabled={busy || !placementChanged || pending === null}
        onClick={() => {
          if (pending !== null) void run(pending, LOCAL_FRAME_TEXT.done);
        }}
      >
        {LOCAL_FRAME_TEXT.applyPlacement}
      </button>
      <button
        type="button"
        disabled={busy || pending === null}
        onClick={() => ui.getState().cancelDraft()}
      >
        {LOCAL_FRAME_TEXT.discard}
      </button>
      <label>
        {LOCAL_FRAME_TEXT.northField}{" "}
        <input
          type="number"
          step="any"
          aria-label={LOCAL_FRAME_TEXT.northField}
          value={northValue}
          onChange={(event) => setNorthText(event.target.value)}
        />
      </label>
      <button type="button" disabled={busy} onClick={onApplyNorth}>
        {LOCAL_FRAME_TEXT.applyNorth}
      </button>
      {busy && <p role="status">{LOCAL_FRAME_TEXT.working}</p>}
      {message !== null && <p role={message.error ? "alert" : "status"}>{message.text}</p>}
    </aside>
  );
}

/** Pending origin and the +X direction line, live while dragging. */
function FramePreview({ ui }: { ui: StoreApi<EditorUiStore> }) {
  const draft = useStore(ui, (state) => state.draft);
  const hover = useStore(ui, (state) => state.hover);
  const [origin, axisPoint] = draft;
  if (origin === undefined) return null;
  const tip = hover ?? axisPoint;
  return (
    <g color="#d0006f" stroke="currentColor" fill="none">
      <path
        data-testid="frame-origin-preview"
        d={`M${svgPoints([origin])}h0`}
        strokeWidth={9}
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {tip !== undefined && (
        <polyline
          data-testid="frame-axis-preview"
          points={svgPoints([origin, tip])}
          strokeWidth={2}
          strokeDasharray="6 3"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </g>
  );
}

export function createLocalFrameTool(context: ToolContext): EditorTool {
  const { ui } = context;
  /** True while the press that placed the origin is held: moving drags the +X direction. */
  let dragging = false;

  function origin(): Point2 {
    return ui.getState().draft[0] ?? [0, 0];
  }

  return {
    onPointerDown(pointer: CanvasPointer) {
      const point = snappedDrawPoint(context, pointer, []);
      if (point === null) return true;
      const current = origin();
      const onOrigin =
        Math.hypot(point[0] - current[0], point[1] - current[1]) * pointer.scale <= HANDLE_HIT_PX;
      if (onOrigin) {
        ui.getState().setDraft([current, ...ui.getState().draft.slice(1)]);
      } else {
        ui.getState().setDraft([point]);
      }
      ui.getState().setHover(null);
      dragging = true;
      return true;
    },
    onPointerMove(pointer) {
      if (!dragging) return;
      ui.getState().setHover(snappedDrawPoint(context, pointer, [origin()]));
    },
    onPointerUp(pointer) {
      if (!dragging) return;
      dragging = false;
      const start = origin();
      const end = snappedDrawPoint(context, pointer, [start]);
      if (end !== null && apart(start, end, pointer.scale)) ui.getState().setDraft([start, end]);
      ui.getState().setHover(null);
    },
    onKeyDown(event) {
      if (event.key !== "Escape" || ui.getState().draft.length === 0) return false;
      dragging = false;
      ui.getState().cancelDraft();
      return true;
    },
    // Keyboard cursor: Enter presses at the origin, the arrows drag, Enter releases on the axis.
    keyboardDrag: true,
    preview: <FramePreview ui={ui} />,
  };
}
