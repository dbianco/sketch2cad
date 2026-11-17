// Dimension tool "Cota" (spec 8.2 and 11.4, PRD CU-04 "cota declarada", REQ-06): the user marks
// the two ends of a dimension, either with two clicks (snapped to endpoints, orthogonally to the
// first point and to the grid, like the line tool) or with one click on a segment of a line or
// polyline (its two ends, as in the fix-length tool; a click that snaps to an endpoint marks a
// point instead). An inline form then takes the declared value in metres ("4,20" or "4.20", V-04)
// and the offset of the dimension line (left of 1 -> 2, negative for the other side), and
// "Crear cota" adds a `dimension` entity on L_DIMENSIONS through the editor store: it is synced
// by the queue and Deshacer removes it like any drawn entity. Esc drops the points.
//
// Keyboard: the keyboard cursor clicks with Enter like in the drawing tools; the form fields are
// normal inputs. The editor gives no tool to a read-only plan (no lease), so nothing is created
// without the lease.
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";
import { DEFAULT_PAPER_SCALE, formatMetres, svgPoints } from "../canvas/EditorCanvas";
import { worldToScreen, type Viewport } from "../canvas/viewport";
import { resolveSnap } from "../panels/SnapSettings";
import { geometryVertices } from "../snapping/snap";
import type { Point2 } from "../store/types";
import { worldPoint, type EditorTool, type ToolContext } from "../ui/editorUi";
import { DISTANCE_MESSAGE, parseDistance } from "../util/parseDistance";
import { hitSegment } from "./FixLengthTool";
import { DraftPreview, apart, snappedDrawPoint } from "./LineTool";
import { DIMENSION_LAYER, dimensionLayer } from "./OcrCropTool";

/**
 * Default offset of the dimension line, in paper mm: twice the 2.5 mm dimension text, so that
 * the text clears the measured line. At 1:100 it is 0.50 m.
 */
export const DEFAULT_OFFSET_MM_PAPER = 5;

export const DIMENSION_TEXT = {
  pickFirst: "Cota: haz clic en el primer punto o en un segmento de una línea o polilínea.",
  pickSecond: "Haz clic en el segundo punto de la cota (Esc para cancelar).",
  form: "Nueva cota",
  measured: (length: number) => `Medida en el plano: ${formatMetres(length)}`,
  value: "Valor declarado de la cota (m)",
  valueHint: "Coma o punto decimal, por ejemplo 4,20",
  offset: "Separación de la línea de cota (m)",
  offsetHint: "A la izquierda del punto 1 al 2; negativa para el otro lado",
  offsetInvalid: "Ingresa una separación numérica, por ejemplo 0,50 o -0,50.",
  create: "Crear cota",
  cancel: "Cancelar",
  samePoint: "Los dos puntos de la cota coinciden: elige otro segundo punto.",
  layerLocked: `La capa ${DIMENSION_LAYER} está bloqueada u oculta: no se puede crear la cota.`,
  done: (value: number) => `Cota de ${formatMetres(value)} creada.`,
} as const;

const OFFSET_PATTERN = /^[+-]?(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)$/;

/** Signed metres of an offset ("0,5", "-0.5", "0"), or `null` when it is not a number. */
export function parseOffset(text: string): number | null {
  const candidate = text.trim();
  if (!OFFSET_PATTERN.test(candidate)) return null;
  const value = Number(candidate.replace(",", "."));
  return Number.isFinite(value) ? value : null;
}

/** Offset as the field shows it: two decimals and a decimal comma. */
function offsetText(value: number): string {
  return value.toFixed(2).replace(".", ",");
}

interface DimensionState {
  /** The two ends once both are marked; the form is open while set. */
  ends: [Point2, Point2] | null;
  notice: { text: string; error: boolean } | null;
}

export function createDimensionTool(context: ToolContext): EditorTool {
  const { ui } = context;
  const tool = createStore<DimensionState>()(() => ({ ends: null, notice: null }));
  const defaultOffset = (DEFAULT_OFFSET_MM_PAPER * (context.paperScale ?? DEFAULT_PAPER_SCALE)) / 1000;

  function cancel(): void {
    ui.getState().cancelDraft();
    tool.setState({ ends: null });
  }

  /** Validate the form and add the dimension; returns an error message, or null when created. */
  function create(valueText: string, offsetInput: string): string | null {
    const { ends } = tool.getState();
    if (ends === null) return null;
    const value = parseDistance(valueText);
    if (value === null) return DISTANCE_MESSAGE;
    const offset = parseOffset(offsetInput);
    if (offset === null) return DIMENSION_TEXT.offsetInvalid;
    const layer = dimensionLayer(context);
    if (layer === null) return DIMENSION_TEXT.layerLocked;
    context.store.getState().addEntity({
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
    });
    tool.setState({ ends: null, notice: { text: DIMENSION_TEXT.done(value), error: false } });
    return null;
  }

  return {
    onPointerDown(pointer) {
      const point = worldPoint(pointer.world);
      if (point === null) return true;
      const [start] = ui.getState().draft;
      if (start === undefined || tool.getState().ends !== null) {
        ui.getState().cancelDraft();
        const snap = resolveSnap(context, point, { anchors: [], exclude: null, scale: pointer.scale });
        const segment = snap.kind === "endpoint" ? null : hitSegment(context, pointer);
        const entity =
          segment === null ? undefined : context.store.getState().entities.byId[segment.entityId];
        if (segment !== null && entity !== undefined) {
          const vertices = geometryVertices(entity.geometry);
          const a = vertices[segment.ends[0]];
          const b = vertices[segment.ends[1]];
          if (a !== undefined && b !== undefined) {
            tool.setState({ ends: [[a[0], a[1]], [b[0], b[1]]], notice: null });
            return true;
          }
        }
        tool.setState({ ends: null, notice: null });
        ui.getState().setDraft([snap.point]);
        return true;
      }
      const end = snappedDrawPoint(context, pointer, [start]);
      if (end === null) return true;
      if (!apart(start, end, pointer.scale)) {
        tool.setState({ notice: { text: DIMENSION_TEXT.samePoint, error: true } });
        return true;
      }
      ui.getState().cancelDraft();
      tool.setState({ ends: [start, end], notice: null });
      return true;
    },
    onPointerMove(pointer) {
      const [start] = ui.getState().draft;
      if (start !== undefined) ui.getState().setHover(snappedDrawPoint(context, pointer, [start]));
    },
    onKeyDown(event) {
      if (event.key !== "Escape") return false;
      if (ui.getState().draft.length === 0 && tool.getState().ends === null) return false;
      cancel();
      return true;
    },
    preview: (
      <>
        <DraftPreview ui={ui} />
        <EndsPreview tool={tool} />
      </>
    ),
    renderOverlay: (viewport) => (
      <DimensionOverlay
        context={context}
        tool={tool}
        viewport={viewport}
        defaultOffset={defaultOffset}
        create={create}
        cancel={cancel}
      />
    ),
  };
}

function EndsPreview({ tool }: { tool: StoreApi<DimensionState> }) {
  const ends = useStore(tool, (state) => state.ends);
  if (ends === null) return null;
  return (
    <polyline
      data-testid="dimension-ends"
      points={svgPoints(ends)}
      fill="none"
      stroke="#ff7a00"
      strokeWidth={3}
      vectorEffect="non-scaling-stroke"
    />
  );
}

interface OverlayProps {
  context: ToolContext;
  tool: StoreApi<DimensionState>;
  viewport: Viewport;
  defaultOffset: number;
  create(valueText: string, offsetText: string): string | null;
  cancel(): void;
}

function DimensionOverlay({ context, tool, viewport, defaultOffset, create, cancel }: OverlayProps) {
  const { ends, notice } = useStore(tool);
  const picking = useStore(context.ui, (state) => state.draft.length > 0);
  let hint: string | null = null;
  if (ends === null) hint = picking ? DIMENSION_TEXT.pickSecond : DIMENSION_TEXT.pickFirst;
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
      {ends !== null && (
        <DimensionForm
          key={`${ends[0].join(",")}:${ends[1].join(",")}`}
          at={worldToScreen(viewport, [(ends[0][0] + ends[1][0]) / 2, (ends[0][1] + ends[1][1]) / 2])}
          length={Math.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1])}
          defaultOffset={defaultOffset}
          create={create}
          cancel={cancel}
        />
      )}
    </div>
  );
}

/** The editor canvas around `element`, to give it the focus back. */
function canvasOf(element: Element | null): SVGSVGElement | null {
  return (
    element
      ?.closest(".editor-canvas-viewport")
      ?.querySelector<SVGSVGElement>("[data-testid='editor-canvas']") ?? null
  );
}

function DimensionForm({
  at,
  length,
  defaultOffset,
  create,
  cancel,
}: {
  at: Point2;
  length: number;
  defaultOffset: number;
  create(valueText: string, offsetText: string): string | null;
  cancel(): void;
}) {
  const [value, setValue] = useState("");
  const [offset, setOffset] = useState(() => offsetText(defaultOffset));
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
    // The press that marked the second point focuses the canvas after this mounts; take it back.
    const timer = setTimeout(() => inputRef.current?.focus());
    return () => clearTimeout(timer);
  }, []);
  function onEscape(event: KeyboardEvent) {
    if (event.key !== "Escape") return;
    canvasOf(inputRef.current)?.focus();
    cancel();
  }
  return (
    <form
      className="editor-dimension-form"
      aria-label={DIMENSION_TEXT.form}
      style={{ position: "absolute", left: at[0] + 12, top: at[1] + 12, background: "#fff" }}
      onSubmit={(event) => {
        event.preventDefault();
        const canvas = canvasOf(inputRef.current);
        const message = create(value, offset);
        setError(message);
        // Once created the form goes away: the keyboard user goes on from the canvas.
        if (message === null) canvas?.focus();
      }}
    >
      <p style={{ margin: 0 }}>{DIMENSION_TEXT.measured(length)}</p>
      <label style={{ display: "block" }}>
        {DIMENSION_TEXT.value}{" "}
        <input
          ref={inputRef}
          type="text"
          inputMode="decimal"
          title={DIMENSION_TEXT.valueHint}
          value={value}
          aria-invalid={error === DISTANCE_MESSAGE}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onEscape}
        />
      </label>
      <label style={{ display: "block" }}>
        {DIMENSION_TEXT.offset}{" "}
        <input
          type="text"
          inputMode="decimal"
          title={DIMENSION_TEXT.offsetHint}
          value={offset}
          aria-invalid={error === DIMENSION_TEXT.offsetInvalid}
          onChange={(event) => setOffset(event.target.value)}
          onKeyDown={onEscape}
        />
      </label>
      {error !== null && (
        <p role="alert" style={{ margin: 0 }}>
          {error}
        </p>
      )}
      <button type="submit">{DIMENSION_TEXT.create}</button>
      <button type="button" onClick={cancel}>
        {DIMENSION_TEXT.cancel}
      </button>
    </form>
  );
}
