// OCR crop tool (spec 10 `ocr_crop`, US-13, REQ-18, H5): the user drags a box over a handwritten
// number of the source image under the canvas; the box goes to `POST /plans/{id}/jobs`
// (`ocr_crop`), the job is polled until it ends, and the panel shows the reading, its confidence
// and a field. The field is filled and focused only when the server says `preselect`
// (confidence >= `ocr.min_confidence`); otherwise it stays empty and the raw text is a hint. The
// user accepts or types the value (comma or point, > 0, V-04) and picks where it goes: a click on
// a segment of a line or polyline, or on a dimension (a single selected line or dimension is
// taken when the reading arrives). "Crear cota" then creates a dimension over that segment with
// `declared_value_m` and `ocr_job_id`, so the server records `source.method = ocr` and whether the
// reading was accepted or corrected; a picked dimension is replaced by the new one. Like the
// server-computed tools it flushes the queue first and reloads the entities, so Deshacer does not
// revert it. A failed job shows its cause and action (features/jobs/JobFailure.tsx) and leaves the
// editor as it was.
//
// Coordinates: the canvas stretches the source preview, which maps 1:1 onto the original image
// (`width_px` x `height_px`; for a PDF its 300 dpi raster), over `SourceImage.bounds` with the
// image's top row at `maxY`. A world point therefore maps linearly to original pixels; the box is
// the pixels the user sees under the rectangle, whatever the calibration's homography.
//
// Reading needs no lease; creating the dimension does (the button is disabled and says why).
// The polling runs in the panel, so switching tools or leaving the editor stops it.
import { useEffect, useId, useRef, useState } from "react";
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";
import { ApiError, api as defaultApi, type ApiClient } from "../../api/client";
import type { components } from "../../api/schema";
import { JobFailure, jobFailureOf, type JobError } from "../../features/jobs/JobFailure";
import { planErrorMessage, problemCode } from "../../features/plans/messages";
import { formatMetres, svgPoints, type CanvasPointer } from "../canvas/EditorCanvas";
import { layerFlags as flagsOf } from "../canvas/layers";
import { holdsLease, planStore } from "../plan/planStore";
import { geometryVertices } from "../snapping/snap";
import { resolveServerId } from "../store/entities";
import {
  SERVER_OPERATION_TEXT,
  flushFailure,
  reloadEntities,
  serverOperationMessage,
} from "../store/serverOps";
import { flushQueue } from "../store/sync";
import type { EditorEntity, EntityId, Point2 } from "../store/types";
import {
  drawingLayer,
  isDrawable,
  type EditorTool,
  type SourceImage,
  type ToolContext,
} from "../ui/editorUi";
import { DISTANCE_MESSAGE, parseDistance } from "../util/parseDistance";
import { hitSegment } from "./FixLengthTool";
import { HANDLE_HIT_PX } from "./VertexEditTool";

type OcrResult = components["schemas"]["OcrResultOut"];
type JobOut = components["schemas"]["JobOut"];

/** Same cadence as the QC and export jobs (JOB_POLL_INTERVAL_MS of UploadPage / ExportPage). */
export const OCR_POLL_INTERVAL_MS = 2000;
/** Box side limits of the API (`OcrCropParams`). */
export const OCR_BOX_MIN_SIDE_PX = 8;
export const OCR_BOX_MAX_SIDE_PX = 2000;
/** Catalog layer of dimensions (spec 12.1). */
export const DIMENSION_LAYER = "L_DIMENSIONS";
/** `reason` of the DELETE of a dimension replaced by an OCR reading. */
export const OCR_REPLACED_REASON = "replaced_by_ocr";

const ratio = (value: number) => value.toFixed(2).replace(".", ",");

const REASON_LABELS: Readonly<Record<string, string>> = {
  empty: "no se leyó ningún texto",
  non_numeric: "el texto no es un número",
  non_positive: "el valor no es mayor que cero",
  ambiguous: "el separador decimal es ambiguo",
};

export const OCR_TEXT = {
  title: "Leer cota (OCR)",
  draw: "Arrastra un recuadro sobre el número en la imagen de origen. Con teclado: Intro en una esquina, flechas e Intro en la opuesta.",
  noImage: "No hay imagen de origen en el lienzo: no se puede recortar.",
  noPlan: "No hay un plano abierto.",
  sending: "Enviando el recorte…",
  reading: "Leyendo la cota…",
  ready: "Lectura lista.",
  failed: "La lectura falló. Puedes seguir editando o recortar de nuevo.",
  pollFailed: {
    cause: "No se pudo consultar el estado de la lectura.",
    action: "Comprueba la conexión y vuelve a recortar.",
  },
  tooSmall: `El recorte es demasiado pequeño: cada lado debe medir al menos ${String(OCR_BOX_MIN_SIDE_PX)} píxeles de la imagen. Acerca la vista o arrastra un recuadro mayor.`,
  tooLarge: `El recorte es demasiado grande: cada lado debe medir como mucho ${String(OCR_BOX_MAX_SIDE_PX)} píxeles de la imagen.`,
  outside: "El recorte debe quedar sobre la imagen de origen.",
  aiOff: "La asistencia de IA está desactivada: no se puede leer la cota.",
  boxInvalid: "El servidor rechazó el recorte: debe quedar dentro de la imagen y medir entre 8 y 2000 píxeles por lado.",
  sourceInvalid: "El servidor rechazó el recorte: la imagen no es la fuente principal del plano.",
  requestFailed: "No se pudo enviar el recorte. Inténtalo de nuevo.",
  rawText: (text: string) => `Texto leído: «${text}»`,
  proposed: (value: number) => `Valor propuesto: ${formatMetres(value)}`,
  noValue: (reason: string | null) =>
    `Sin valor propuesto: ${(reason !== null ? REASON_LABELS[reason] : undefined) ?? "lectura no válida"}.`,
  confidence: (c: number, min: number) => `Confianza: ${ratio(c)} (mínima para proponer: ${ratio(min)})`,
  field: "Medida (m)",
  hint: "Coma o punto decimal, por ejemplo 4,20.",
  rawHint: (text: string) => `Lectura sin proponer: «${text}». Escribe la medida; coma o punto decimal.`,
  target: (label: string) => `Destino: ${label}`,
  pickTarget: "Haz clic en el segmento o la cota a la que corresponde la medida.",
  segment: (n: number, length: number) => `segmento ${String(n)} (${formatMetres(length)})`,
  dimension: (length: number) => `cota existente (${formatMetres(length)}), se sustituirá`,
  accept: "Crear cota",
  working: "Creando la cota…",
  discard: "Descartar",
  needsLease: "Para crear la cota necesitas el permiso de edición del plano.",
  layerLocked: `La capa ${DIMENSION_LAYER} está bloqueada u oculta: no se puede crear la cota.`,
  targetGone: "El destino elegido ya no existe. Elige otro.",
  replaceFailed: "La cota anterior no se pudo eliminar; bórrala a mano.",
  done: (value: number, corrected: boolean) =>
    `Cota creada: ${formatMetres(value)} (${corrected ? "valor corregido" : "lectura OCR aceptada"}).`,
} as const;

export type BoxPx = [number, number, number, number];

/** Where an accepted value goes: a segment of a line/polyline, or a dimension to replace. */
export type OcrTarget =
  | { kind: "segment"; entityId: EntityId; ends: [number, number] }
  | { kind: "dimension"; entityId: EntityId };

type Phase =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "polling"; jobId: string }
  | { kind: "result"; jobId: string; result: OcrResult }
  | { kind: "failed"; error: JobError };

interface OcrState {
  phase: Phase;
  /** Corners (world) of the box being dragged. */
  drag: [Point2, Point2] | null;
  target: OcrTarget | null;
  busy: boolean;
  notice: { text: string; error: boolean } | null;
}

/** Removes the float noise of the viewport transform before rounding to pixels. */
const clean = (value: number) => Math.round(value * 1e6) / 1e6;

/**
 * Original-image pixels `[x0, y0, x1, y1]` under the world rectangle `a`-`b` (rounded outwards and
 * clipped to the image), or the Spanish message of why it cannot be sent.
 */
export function boxToSourcePx(a: Point2, b: Point2, image: SourceImage): BoxPx | string {
  const { bounds, widthPx, heightPx } = image;
  const fx = widthPx / (bounds.maxX - bounds.minX);
  const fy = heightPx / (bounds.maxY - bounds.minY);
  if (!Number.isFinite(fx) || !Number.isFinite(fy) || fx <= 0 || fy <= 0) return OCR_TEXT.noImage;
  const clip = (v: number, max: number) => Math.min(max, Math.max(0, v));
  const x0 = clip(Math.floor(clean((Math.min(a[0], b[0]) - bounds.minX) * fx)), widthPx);
  const x1 = clip(Math.ceil(clean((Math.max(a[0], b[0]) - bounds.minX) * fx)), widthPx);
  const y0 = clip(Math.floor(clean((bounds.maxY - Math.max(a[1], b[1])) * fy)), heightPx);
  const y1 = clip(Math.ceil(clean((bounds.maxY - Math.min(a[1], b[1])) * fy)), heightPx);
  if (x1 <= x0 || y1 <= y0) return OCR_TEXT.outside;
  if (x1 - x0 < OCR_BOX_MIN_SIDE_PX || y1 - y0 < OCR_BOX_MIN_SIDE_PX) return OCR_TEXT.tooSmall;
  if (x1 - x0 > OCR_BOX_MAX_SIDE_PX || y1 - y0 > OCR_BOX_MAX_SIDE_PX) return OCR_TEXT.tooLarge;
  return [x0, y0, x1, y1];
}

/** Spanish message of a rejected `ocr_crop` request. */
export function ocrRequestMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return OCR_TEXT.aiOff;
  if (error instanceof ApiError && error.status === 422) {
    const code = problemCode(error);
    if (code === "ocr_source_invalid") return OCR_TEXT.sourceInvalid;
    return OCR_TEXT.boxInvalid;
  }
  return planErrorMessage(error, OCR_TEXT.requestFailed);
}

function usable(context: ToolContext, entity: EditorEntity | undefined): entity is EditorEntity {
  if (entity === undefined || entity.deleted) return false;
  const flags = flagsOf(context.ui.getState().layerFlags, entity.layer);
  return flags.visible && !flags.locked;
}

function distanceToSegment(p: Point2, a: Point2, b: Point2): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const length2 = dx * dx + dy * dy;
  const t =
    length2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

/** Target under `pointer`: a dimension under it, else a segment, else a dimension nearby. */
export function hitTarget(context: ToolContext, pointer: CanvasPointer): OcrTarget | null {
  const { byId, order } = context.store.getState().entities;
  const under = pointer.entityId === null ? undefined : byId[pointer.entityId];
  if (usable(context, under) && under.geometry.kind === "dimension") {
    return { kind: "dimension", entityId: under.id };
  }
  const segment = hitSegment(context, pointer);
  if (segment !== null) return { kind: "segment", ...segment };
  let best: { id: EntityId; px: number } | null = null;
  for (const id of order) {
    const entity = byId[id];
    if (!usable(context, entity) || entity.geometry.kind !== "dimension") continue;
    const { p1_m, p2_m } = entity.geometry;
    const px = distanceToSegment(pointer.world, p1_m, p2_m) * pointer.scale;
    if (px <= HANDLE_HIT_PX && (best === null || px < best.px)) best = { id, px };
  }
  return best === null ? null : { kind: "dimension", entityId: best.id };
}

/** Target of a single selected line (or 2-vertex open polyline) or dimension, if any. */
function selectedTarget(context: ToolContext): OcrTarget | null {
  const { selection, entities } = context.store.getState();
  const entity = selection.length === 1 ? entities.byId[selection[0] as EntityId] : undefined;
  if (!usable(context, entity)) return null;
  const { geometry } = entity;
  if (geometry.kind === "dimension") return { kind: "dimension", entityId: entity.id };
  const single =
    geometry.kind === "line" || (geometry.kind === "polyline" && !geometry.closed);
  return single && geometry.coords_m.length === 2
    ? { kind: "segment", entityId: entity.id, ends: [0, 1] }
    : null;
}

function targetEnds(context: ToolContext, target: OcrTarget): [Point2, Point2] | null {
  const entity = context.store.getState().entities.byId[target.entityId];
  if (!usable(context, entity)) return null;
  const vertices = geometryVertices(entity.geometry);
  const [i, j] = target.kind === "segment" ? target.ends : [0, 1];
  const a = vertices[i];
  const b = vertices[j];
  return a === undefined || b === undefined ? null : [a, b];
}

/** Layer of a new dimension: L_DIMENSIONS when the catalog has it, else the drawing layer. */
function dimensionLayer(context: ToolContext): string | null {
  const ui = context.ui.getState();
  if (!context.catalog.layers.some((layer) => layer.name === DIMENSION_LAYER)) {
    return drawingLayer(ui, context.catalog);
  }
  return isDrawable(ui, context.catalog, DIMENSION_LAYER) ? DIMENSION_LAYER : null;
}

const IDLE: Pick<OcrState, "phase" | "drag" | "target"> = {
  phase: { kind: "idle" },
  drag: null,
  target: null,
};

export function createOcrCropTool(context: ToolContext): EditorTool {
  const ocr = createStore<OcrState>()(() => ({ ...IDLE, busy: false, notice: null }));
  const api = context.api ?? defaultApi;
  const plan = context.plan ?? planStore;
  const fail = (text: string) => ocr.setState({ busy: false, notice: { text, error: true } });

  function finish(job: JobOut): void {
    const { phase } = ocr.getState();
    if (phase.kind !== "polling" && phase.kind !== "sending") return;
    if (phase.kind === "polling" && phase.jobId !== job.id) return;
    if (job.status === "succeeded" && job.result != null) {
      const result = job.result;
      ocr.setState({ phase: { kind: "result", jobId: job.id, result }, target: selectedTarget(context) });
    } else {
      ocr.setState({ phase: { kind: "failed", error: jobFailureOf(job) } });
    }
  }

  async function submit(a: Point2, b: Point2): Promise<void> {
    const image = context.sourceImage;
    const planId = plan.getState().planId;
    if (image === undefined) return fail(OCR_TEXT.noImage);
    if (planId === null) return fail(OCR_TEXT.noPlan);
    const box = boxToSourcePx(a, b, image);
    if (typeof box === "string") return fail(box);
    ocr.setState({ phase: { kind: "sending" }, notice: null });
    try {
      const job = await api.post("/api/v1/plans/{plan_id}/jobs", {
        path: { plan_id: planId },
        body: { kind: "ocr_crop", params: { source_id: image.sourceId, box_px: box } },
      });
      if (job.status === "succeeded" || job.status === "failed") finish(job);
      else ocr.setState({ phase: { kind: "polling", jobId: job.id } });
    } catch (error) {
      ocr.setState({ phase: { kind: "idle" } });
      fail(ocrRequestMessage(error));
    }
  }

  function dismiss(): void {
    if (!ocr.getState().busy) ocr.setState({ ...IDLE, notice: null });
  }

  async function accept(text: string): Promise<void> {
    const { phase, target, busy } = ocr.getState();
    if (phase.kind !== "result" || busy) return;
    const value = parseDistance(text);
    if (value === null) return fail(DISTANCE_MESSAGE);
    if (target === null) return fail(OCR_TEXT.pickTarget);
    const state = plan.getState();
    if (state.readOnly || !holdsLease(state)) return fail(OCR_TEXT.needsLease);
    const revisionId = state.revisionId ?? context.store.getState().revisionId;
    if (revisionId === null || revisionId === "") return fail(OCR_TEXT.noPlan);
    ocr.setState({ busy: true, notice: null });
    const sync = { revisionId };
    const flushed = await flushQueue(api, context.store, sync);
    if (flushed.status !== "drained") return fail(flushFailure(flushed).message);
    const { byId } = context.store.getState().entities;
    const entity = byId[target.entityId];
    const ends = targetEnds(context, target);
    if (entity === undefined || ends === null) return fail(OCR_TEXT.targetGone);
    const replaced = target.kind === "dimension" && entity.geometry.kind === "dimension";
    const layer = replaced ? entity.layer : dimensionLayer(context);
    if (layer === null) return fail(OCR_TEXT.layerLocked);
    const offset = entity.geometry.kind === "dimension" ? entity.geometry.offset_m : 0;
    try {
      await api.post("/api/v1/revisions/{revision_id}/entities", {
        path: { revision_id: revisionId },
        body: {
          entities: [
            {
              type: "dimension",
              layer,
              attributes: {},
              geometry: {
                kind: "dimension",
                p1_m: ends[0],
                p2_m: ends[1],
                offset_m: offset,
                declared_value_m: value,
              },
              ocr_job_id: phase.jobId,
            },
          ],
        },
      });
    } catch (error) {
      return fail(serverOperationMessage(error));
    }
    const problems: string[] = [];
    if (replaced) {
      try {
        await api.delete("/api/v1/entities/{entity_id}", {
          path: { entity_id: resolveServerId(target.entityId, context.store.getState().serverIds) },
          query: { reason: OCR_REPLACED_REASON },
        });
      } catch {
        problems.push(OCR_TEXT.replaceFailed);
      }
    }
    const reloaded = await reloadEntities(api, context.store, sync);
    if (!reloaded.ok) problems.push(reloaded.message);
    const done = OCR_TEXT.done(value, value !== phase.result.value_m);
    ocr.setState({
      ...IDLE,
      busy: false,
      notice: { text: [done, ...problems].join(" "), error: problems.length > 0 },
    });
  }

  return {
    // The keyboard cursor drags the box (Enter, arrows, Enter) but clicks a target.
    get keyboardDrag() {
      return ocr.getState().phase.kind !== "result";
    },
    onPointerDown(pointer) {
      const { phase, busy } = ocr.getState();
      if (busy || phase.kind === "sending" || phase.kind === "polling") return true;
      if (phase.kind === "result") {
        const target = hitTarget(context, pointer);
        if (target !== null) ocr.setState({ target, notice: null });
        return true;
      }
      ocr.setState({ ...IDLE, drag: [pointer.world, pointer.world], notice: null });
      return true;
    },
    onPointerMove(pointer) {
      const { drag } = ocr.getState();
      if (drag !== null) ocr.setState({ drag: [drag[0], pointer.world] });
    },
    onPointerUp(pointer) {
      const { drag } = ocr.getState();
      if (drag === null) return;
      ocr.setState({ drag: null });
      void submit(drag[0], pointer.world);
    },
    onKeyDown(event) {
      if (event.key !== "Escape") return false;
      const { drag, phase } = ocr.getState();
      if (drag !== null) ocr.setState({ drag: null });
      else if (phase.kind === "result" || phase.kind === "failed") dismiss();
      else return false;
      return true;
    },
    preview: <OcrPreview context={context} ocr={ocr} />,
    panel: (
      <OcrPanel
        context={context}
        ocr={ocr}
        api={api}
        finish={finish}
        accept={accept}
        dismiss={dismiss}
      />
    ),
  };
}

interface ViewProps {
  context: ToolContext;
  ocr: StoreApi<OcrState>;
}

function useTargetEnds({ context, ocr }: ViewProps): [Point2, Point2] | null {
  const target = useStore(ocr, (state) => state.target);
  // Re-render when the target entity or the layer flags change.
  useStore(context.store, (state) =>
    target === null ? undefined : state.entities.byId[target.entityId],
  );
  useStore(context.ui, (state) => state.layerFlags);
  return target === null ? null : targetEnds(context, target);
}

function OcrPreview(props: ViewProps) {
  const drag = useStore(props.ocr, (state) => state.drag);
  const ends = useTargetEnds(props);
  return (
    <>
      {drag !== null && (
        <rect
          data-testid="ocr-crop-box"
          x={Math.min(drag[0][0], drag[1][0])}
          y={-Math.max(drag[0][1], drag[1][1])}
          width={Math.abs(drag[1][0] - drag[0][0])}
          height={Math.abs(drag[1][1] - drag[0][1])}
          fill="none"
          stroke="#0B6BCB"
          strokeDasharray="4 2"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
        />
      )}
      {ends !== null && (
        <polyline
          data-testid="ocr-target"
          points={svgPoints(ends)}
          fill="none"
          stroke="#0B6BCB"
          strokeWidth={4}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </>
  );
}

/** Poll the job of a `polling` phase until it ends; stops when the panel unmounts. */
function useJobPolling(
  ocr: StoreApi<OcrState>,
  api: Pick<ApiClient, "get">,
  finish: (job: JobOut) => void,
): void {
  const jobId = useStore(ocr, (state) => (state.phase.kind === "polling" ? state.phase.jobId : null));
  useEffect(() => {
    if (jobId === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll(id: string): Promise<void> {
      try {
        const job = await api.get("/api/v1/jobs/{job_id}", {
          path: { job_id: id },
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (job.status === "succeeded" || job.status === "failed") finish(job);
        else timer = setTimeout(() => void poll(id), OCR_POLL_INTERVAL_MS);
      } catch {
        if (!controller.signal.aborted) {
          ocr.setState({ phase: { kind: "failed", error: OCR_TEXT.pollFailed } });
        }
      }
    }
    timer = setTimeout(() => void poll(jobId), OCR_POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [jobId, api, finish, ocr]);
}

function OcrPanel({
  context,
  ocr,
  api,
  finish,
  accept,
  dismiss,
}: ViewProps & {
  api: Pick<ApiClient, "get">;
  finish(job: JobOut): void;
  accept(text: string): Promise<void>;
  dismiss(): void;
}) {
  useJobPolling(ocr, api, finish);
  const { phase, notice, target } = useStore(ocr);
  let status: string = OCR_TEXT.draw;
  if (context.sourceImage === undefined) status = OCR_TEXT.noImage;
  else if (phase.kind === "sending") status = OCR_TEXT.sending;
  else if (phase.kind === "polling") status = OCR_TEXT.reading;
  else if (phase.kind === "result") status = target === null ? `${OCR_TEXT.ready} ${OCR_TEXT.pickTarget}` : OCR_TEXT.ready;
  else if (phase.kind === "failed") status = OCR_TEXT.failed;
  return (
    <aside className="editor-ocr" aria-label={OCR_TEXT.title} style={{ width: 280, padding: 8 }}>
      <h2 style={{ fontSize: 14, margin: "0 0 6px" }}>{OCR_TEXT.title}</h2>
      <p role="status" aria-live="polite" style={{ margin: "0 0 6px" }}>
        {status}
      </p>
      {notice !== null && (
        <p role={notice.error ? "alert" : "status"} style={{ margin: "0 0 6px" }}>
          {notice.text}
        </p>
      )}
      {phase.kind === "failed" && <JobFailure error={phase.error} onDismiss={dismiss} />}
      {phase.kind === "result" && (
        <ResultForm
          key={phase.jobId}
          context={context}
          ocr={ocr}
          result={phase.result}
          accept={accept}
          dismiss={dismiss}
        />
      )}
    </aside>
  );
}

/** Text of a value for the field, with the Spanish decimal comma. */
function fieldText(value: number): string {
  return String(value).replace(".", ",");
}

function ResultForm({
  context,
  ocr,
  result,
  accept,
  dismiss,
}: ViewProps & { result: OcrResult; accept(text: string): Promise<void>; dismiss(): void }) {
  const preselect = result.preselect && result.value_m !== null;
  const [text, setText] = useState(preselect && result.value_m !== null ? fieldText(result.value_m) : "");
  const inputRef = useRef<HTMLInputElement>(null);
  const hintId = useId();
  const leaseId = useId();
  const busy = useStore(ocr, (state) => state.busy);
  const target = useStore(ocr, (state) => state.target);
  const ends = useTargetEnds({ context, ocr });
  const canEdit = useStore(context.plan ?? planStore, (s) => !s.readOnly && holdsLease(s));

  useEffect(() => {
    if (!preselect) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [preselect]);

  let targetText: string = OCR_TEXT.pickTarget;
  if (target !== null && ends !== null) {
    const length = Math.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1]);
    targetText = OCR_TEXT.target(
      target.kind === "dimension"
        ? OCR_TEXT.dimension(length)
        : OCR_TEXT.segment(target.ends[0] + 1, length),
    );
  }

  return (
    <form
      aria-label={OCR_TEXT.title}
      onSubmit={(event) => {
        event.preventDefault();
        void accept(text);
      }}
    >
      <p style={{ margin: 0 }}>{OCR_TEXT.rawText(result.raw_text)}</p>
      <p style={{ margin: 0 }}>
        {result.value_m !== null ? OCR_TEXT.proposed(result.value_m) : OCR_TEXT.noValue(result.reason)}
      </p>
      <p style={{ margin: "0 0 6px" }}>{OCR_TEXT.confidence(result.confidence, result.min_confidence)}</p>
      <label>
        {OCR_TEXT.field}{" "}
        <input
          ref={inputRef}
          type="text"
          inputMode="decimal"
          aria-describedby={hintId}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") dismiss();
          }}
        />
      </label>
      <p id={hintId} style={{ margin: "2px 0 6px", fontSize: 12 }}>
        {preselect ? OCR_TEXT.hint : OCR_TEXT.rawHint(result.raw_text)}
      </p>
      <p style={{ margin: "0 0 6px" }}>{targetText}</p>
      {!canEdit && (
        <p id={leaseId} style={{ margin: "0 0 6px", color: "#7A3E00" }}>
          {OCR_TEXT.needsLease}
        </p>
      )}
      <button type="submit" disabled={busy || !canEdit} aria-describedby={canEdit ? undefined : leaseId}>
        {busy ? OCR_TEXT.working : OCR_TEXT.accept}
      </button>{" "}
      <button type="button" disabled={busy} onClick={dismiss}>
        {OCR_TEXT.discard}
      </button>
      <p style={{ margin: "6px 0 0", fontSize: 12 }}>{SERVER_OPERATION_TEXT.noUndo}</p>
    </form>
  );
}
