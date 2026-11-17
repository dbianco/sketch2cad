// Measure tool "Medir" (spec 8.2 "medir (sin crear entidad)", PRD REQ editor tools): the user
// marks two points, with two clicks or a press-drag-release, and reads the distance between
// them. Both points are snapped like the line tool's (endpoint, grid, and the second one also
// orthogonally to the first). Nothing reaches the editor store: no entity, no history step, no
// API operation. One measurement is shown at a time: a click after a finished measurement starts
// the next one, and Esc, switching tool or leaving the editor drops it.
//
// The distance is shown only in calibrated metres, so only when the plan's calibration is metric
// (planStore `metricCalibration`: accepted `as_metric` with a method other than `none`). Without
// one the canvas coordinates are not calibrated metres, so the tool takes no points and says why
// instead of showing a number that looks metric.
//
// Keyboard: the keyboard cursor clicks with Enter like in the drawing tools; Esc clears.
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";
import { formatMetres, svgPoints, type CanvasPointer } from "../canvas/EditorCanvas";
import { worldToScreen, type Viewport } from "../canvas/viewport";
import { createPlanStore, type PlanStore } from "../plan/planStore";
import { resolveSnap } from "../panels/SnapSettings";
import type { Point2 } from "../store/types";
import { worldPoint, type EditorTool, type EditorUiStore, type ToolContext } from "../ui/editorUi";
import { DraftPreview, apart, snappedDrawPoint } from "./LineTool";

export const MEASURE_TEXT = {
  pickFirst: "Medir: haz clic en el primer punto. No se crea ninguna entidad.",
  pickSecond: "Haz clic en el segundo punto (Esc para cancelar).",
  result: (distance: number) => `Distancia: ${formatMetres(distance)}`,
  next: "Haz clic para empezar otra medición o pulsa Esc para borrarla.",
  notMetric:
    "Este plano no tiene una calibración métrica: no se pueden medir distancias en metros. " +
    "Acepta una calibración métrica para usar Medir.",
} as const;

/** Colour of the finished measurement (the keyboard cursor's blue, apart from entity colours). */
const MEASURE_COLOR = "#0B57D0";

/** Distance in metres between two world points. */
export function measureDistance(a: Point2, b: Point2): number {
  return Math.hypot(b[0] - a[0], b[1] - a[1]);
}

interface MeasureState {
  /** The two points of the finished measurement; null while none is shown. */
  ends: [Point2, Point2] | null;
}

export function createMeasureTool(context: ToolContext): EditorTool {
  const { ui } = context;
  // Without a plan store nothing says the plan is calibrated: the tool stays non-metric.
  const plan = context.plan ?? createPlanStore();
  const tool = createStore<MeasureState>()(() => ({ ends: null }));
  /** True while the press that placed the first point is held: its release may end a drag. */
  let dragging = false;

  const metric = () => plan.getState().metricCalibration;

  function finish(start: Point2, end: Point2, scale: number): void {
    if (!apart(start, end, scale)) return;
    ui.getState().cancelDraft();
    tool.setState({ ends: [start, end] });
  }

  return {
    onPointerDown(pointer: CanvasPointer) {
      if (!metric()) return true;
      const [start] = ui.getState().draft;
      if (start === undefined) {
        const point = worldPoint(pointer.world);
        if (point === null) return true;
        const snapped = resolveSnap(context, point, {
          anchors: [],
          exclude: null,
          scale: pointer.scale,
        }).point;
        // A new measurement replaces the finished one: only one is shown at a time.
        tool.setState({ ends: null });
        ui.getState().setDraft([snapped]);
        dragging = true;
        return true;
      }
      const end = snappedDrawPoint(context, pointer, [start]);
      if (end !== null) finish(start, end, pointer.scale);
      return true;
    },
    onPointerMove(pointer) {
      const [start] = ui.getState().draft;
      if (start !== undefined) ui.getState().setHover(snappedDrawPoint(context, pointer, [start]));
    },
    onPointerUp(pointer) {
      const [start] = ui.getState().draft;
      const end = start === undefined ? null : snappedDrawPoint(context, pointer, [start]);
      if (dragging && start !== undefined && end !== null) finish(start, end, pointer.scale);
      dragging = false;
    },
    onKeyDown(event) {
      if (event.key !== "Escape") return false;
      if (ui.getState().draft.length === 0 && tool.getState().ends === null) return false;
      ui.getState().cancelDraft();
      tool.setState({ ends: null });
      return true;
    },
    preview: (
      <>
        <DraftPreview ui={ui} />
        <MeasurePreview tool={tool} />
      </>
    ),
    renderOverlay: (viewport) => (
      <MeasureOverlay ui={ui} plan={plan} tool={tool} viewport={viewport} />
    ),
  };
}

/** The finished measurement: a solid segment with a dot at each end. */
function MeasurePreview({ tool }: { tool: StoreApi<MeasureState> }) {
  const ends = useStore(tool, (state) => state.ends);
  if (ends === null) return null;
  return (
    <g data-testid="measure-segment" color={MEASURE_COLOR} stroke="currentColor" fill="none">
      <polyline points={svgPoints(ends)} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      {ends.map((end, index) => (
        <path
          key={index}
          d={`M${String(end[0])} ${String(-end[1])}h0`}
          strokeLinecap="round"
          strokeWidth={7}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </g>
  );
}

interface OverlayProps {
  ui: StoreApi<EditorUiStore>;
  plan: StoreApi<PlanStore>;
  tool: StoreApi<MeasureState>;
  viewport: Viewport;
}

/**
 * Hint or result in the corner (a polite status: it changes on clicks only) and the distance
 * next to the segment, which follows the pointer while the second point is chosen (hidden from
 * screen readers, which would otherwise hear every pointer move).
 */
function MeasureOverlay({ ui, plan, tool, viewport }: OverlayProps) {
  const metric = useStore(plan, (state) => state.metricCalibration);
  const ends = useStore(tool, (state) => state.ends);
  const start = useStore(ui, (state) => state.draft[0]);
  const hover = useStore(ui, (state) => state.hover);

  let status: string;
  if (!metric) status = MEASURE_TEXT.notMetric;
  else if (ends !== null) status = MEASURE_TEXT.result(measureDistance(ends[0], ends[1]));
  else status = start === undefined ? MEASURE_TEXT.pickFirst : MEASURE_TEXT.pickSecond;

  let segment: [Point2, Point2] | null = null;
  if (metric) segment = ends ?? (start !== undefined && hover !== null ? [start, hover] : null);

  return (
    <div style={{ position: "absolute", left: 0, top: 0, width: 0, height: 0 }}>
      <div
        data-testid="measure-status"
        style={{ position: "absolute", left: 8, top: 8, width: 360, pointerEvents: "none" }}
      >
        <p role="status" style={{ margin: 0 }}>
          {status}
        </p>
        {metric && ends !== null && <p style={{ margin: 0 }}>{MEASURE_TEXT.next}</p>}
      </div>
      {segment !== null && <DistanceLabel segment={segment} viewport={viewport} />}
    </div>
  );
}

function DistanceLabel({ segment, viewport }: { segment: [Point2, Point2]; viewport: Viewport }) {
  const [a, b] = segment;
  const at = worldToScreen(viewport, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
  return (
    <span
      data-testid="measure-label"
      aria-hidden="true"
      style={{
        position: "absolute",
        left: at[0] + 8,
        top: at[1] + 8,
        padding: "1px 4px",
        background: "#fff",
        color: MEASURE_COLOR,
        border: `1px solid ${MEASURE_COLOR}`,
        borderRadius: 3,
        whiteSpace: "nowrap",
        pointerEvents: "none",
      }}
    >
      {formatMetres(measureDistance(a, b))}
    </span>
  );
}
