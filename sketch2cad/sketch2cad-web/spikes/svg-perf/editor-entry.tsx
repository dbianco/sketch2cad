// H2-12 harness page (task 001-068): mounts the REAL EditorCanvas with a synthetic document of
// 2000 entities (same seeded generator and mix as index.html, loaded through the store's `load`)
// and drives its camera with the canvas's own input paths: wheel events at the canvas centre
// zoom it, a middle-button pointer drag pans it. EditorCanvas is used unmodified (no test hook);
// the page reads the committed camera back from the `<svg>` viewBox. Built by editor-run.ts.
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import catalogJson from "../../src/editor/fixtures/catalog-default-1.json";
import { EditorCanvas, WHEEL_ZOOM_PER_PX, type Catalog } from "../../src/editor/canvas/EditorCanvas";
import { createEditorStore } from "../../src/editor/store/entities";
import type { EntityId, EntityOut, Geometry, Point2 } from "../../src/editor/store/types";

const SEED = 20261025;
const WORLD_W = 60;
const WORLD_H = 40;
const COUNTS = { polylines: 1200, lines: 600, symbols: 200 };
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 8;
/** Symbols of the default catalog that play the spike's evidence marker, door and vehicle. */
const SYMBOL_IDS = ["evidence_marker", "door", "generic_vehicle"] as const;
const REVISION_ID = "00000000-0000-4000-8000-000000000068";
const CATALOG = catalogJson as Catalog;
const MIDDLE_BUTTON = 1;
const MIDDLE_BUTTONS_MASK = 4;
const POINTER_ID = 1;

interface Options {
  durationMs: number;
  warmupMs: number;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function entityOut(index: number, layer: string, geometry: Geometry): EntityOut {
  return {
    id: `e-${String(index)}`,
    type: geometry.kind,
    layer,
    geometry,
    attributes: {},
    confidence: null,
    constraint: null,
    created_by: null,
    deleted_at: null,
    discrepancy: null,
    evidence_id: null,
    image_region: null,
    metric_origin: "none",
    model_version: null,
    review_status: "accepted",
    revision_id: REVISION_ID,
    revision_number: 1,
    source: { method: "manual" },
    updated_at: "2026-10-25T00:00:00Z",
  };
}

/** Same random sequence as index.html's buildScene; coordinates are world metres. */
function buildEntities(): EntityOut[] {
  const rnd = mulberry32(SEED);
  const range = (min: number, max: number) => min + rnd() * (max - min);
  const round = (n: number) => Number(n.toFixed(3));
  const entities: EntityOut[] = [];
  for (let i = 0; i < COUNTS.polylines; i++) {
    const n = 4 + Math.floor(rnd() * 9);
    let x = range(0, WORLD_W);
    let y = range(0, WORLD_H);
    const coords: Point2[] = [];
    for (let v = 0; v < n; v++) {
      coords.push([round(x), round(y)]);
      const angle = rnd() * Math.PI * 2;
      const len = range(0.5, 4);
      x = Math.min(WORLD_W, Math.max(0, x + Math.cos(angle) * len));
      y = Math.min(WORLD_H, Math.max(0, y + Math.sin(angle) * len));
    }
    entities.push(
      entityOut(entities.length, "L_STRUCTURE", { kind: "polyline", coords_m: coords, closed: false }),
    );
  }
  for (let i = 0; i < COUNTS.lines; i++) {
    const x1 = range(0, WORLD_W);
    const y1 = range(0, WORLD_H);
    const angle = rnd() * Math.PI * 2;
    const len = range(0.5, 8);
    const coords: Point2[] = [
      [round(x1), round(y1)],
      [round(x1 + Math.cos(angle) * len), round(y1 + Math.sin(angle) * len)],
    ];
    const layer = i % 3 === 0 ? "L_ANNOTATIONS" : "L_STRUCTURE";
    entities.push(entityOut(entities.length, layer, { kind: "line", coords_m: coords }));
  }
  for (let i = 0; i < COUNTS.symbols; i++) {
    const anchor: Point2 = [round(range(1, WORLD_W - 1)), round(range(1, WORLD_H - 1))];
    const rotation = Math.floor(rnd() * 360);
    const symbolId = SYMBOL_IDS[i % 3] ?? "evidence_marker";
    const symbol = CATALOG.symbols.find((s) => s.symbol_id === symbolId);
    if (symbol === undefined) throw new Error(`Symbol ${symbolId} missing from the catalog fixture`);
    const attributes: Record<string, number> =
      symbolId === "evidence_marker" ? { evidence_number: i + 1 } : {};
    entities.push(
      entityOut(entities.length, symbol.default_layer, {
        kind: "symbol",
        symbol_id: symbolId,
        anchor_m: anchor,
        rotation_deg: rotation,
        scale: 1,
        attributes,
      }),
    );
  }
  return entities;
}

/** Same route as index.html: geometric zoom 0.5x-8x twice in 10 s while the centre pans. */
function cameraAt(t: number): { zoom: number; cx: number; cy: number } {
  const s = t / 1000;
  const phase = 0.5 - 0.5 * Math.cos((2 * Math.PI * s) / 5);
  const zoom = Math.exp(Math.log(ZOOM_MIN) + phase * Math.log(ZOOM_MAX / ZOOM_MIN));
  const cx = WORLD_W / 2 + 20 * Math.sin((2 * Math.PI * s) / 3.3);
  const cy = WORLD_H / 2 + 12 * Math.sin((2 * Math.PI * s) / 4.1 + 1);
  return { zoom, cx, cy };
}

function percentile(sorted: readonly number[], p: number): number {
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, idx)] ?? 0;
}

function summarize(deltas: readonly number[]) {
  const sorted = [...deltas].sort((a, b) => a - b);
  const median = percentile(sorted, 50);
  const total = deltas.reduce((acc, d) => acc + d, 0);
  return {
    median_frame_ms: Number(median.toFixed(3)),
    median_fps: Number((1000 / median).toFixed(2)),
    mean_fps: Number(((1000 * deltas.length) / total).toFixed(2)),
    p95_frame_ms: Number(percentile(sorted, 95).toFixed(3)),
    p99_frame_ms: Number(percentile(sorted, 99).toFixed(3)),
    max_frame_ms: Number((sorted[sorted.length - 1] ?? 0).toFixed(3)),
    frames_over_25ms: deltas.filter((d) => d > 25).length,
    frames: deltas.length,
    duration_s: Number((total / 1000).toFixed(3)),
  };
}

/** Synchronous work per frame (event dispatch + React render + commit), excluding paint. */
function summarizeWork(samples: readonly number[]) {
  const sorted = [...samples].sort((a, b) => a - b);
  return {
    median_ms: Number(percentile(sorted, 50).toFixed(3)),
    p95_ms: Number(percentile(sorted, 95).toFixed(3)),
    max_ms: Number((sorted[sorted.length - 1] ?? 0).toFixed(3)),
  };
}

const nextFrame = () => new Promise<number>((resolve) => requestAnimationFrame(resolve));

/** Median rAF interval on an idle page approximates the display refresh rate. */
async function probeRefresh(ms = 1500): Promise<number> {
  const deltas: number[] = [];
  let last = await nextFrame();
  const start = last;
  while (last - start < ms) {
    const now = await nextFrame();
    deltas.push(now - last);
    last = now;
  }
  return Number((1000 / percentile([...deltas].sort((a, b) => a - b), 50)).toFixed(1));
}

const store = createEditorStore();
const entities = buildEntities();
let mounted = false;

function canvasElement(): SVGSVGElement {
  const svg = document.querySelector<SVGSVGElement>("[data-testid='editor-canvas']");
  if (svg === null) throw new Error("EditorCanvas is not mounted");
  return svg;
}

/** Camera the canvas has committed, read back from its `viewBox` (metres, Y down). */
function renderedCamera(svg: SVGSVGElement): { centerX: number; centerY: number; scale: number } {
  const [x = 0, y = 0, w = 1, h = 1] = (svg.getAttribute("viewBox") ?? "").split(" ").map(Number);
  return { centerX: x + w / 2, centerY: -(y + h / 2), scale: svg.width.baseVal.value / w };
}

/** Initial mount: store load + first React render and commit, then the first painted frame. */
async function mountEditor() {
  if (mounted) throw new Error("Already mounted");
  mounted = true;
  const root = document.getElementById("root");
  if (root === null) throw new Error("#root missing");
  await nextFrame();
  const t0 = performance.now();
  store.getState().load(REVISION_ID, entities);
  const t1 = performance.now();
  flushSync(() =>
    createRoot(root).render(
      <EditorCanvas
        catalog={CATALOG}
        store={store}
        width={window.innerWidth}
        height={window.innerHeight}
      />,
    ),
  );
  const t2 = performance.now();
  await nextFrame();
  await nextFrame();
  const t3 = performance.now();
  return {
    entities: entities.length,
    rendered_entity_elements: document.querySelectorAll("[data-entity-id]").length,
    store_load_ms: Number((t1 - t0).toFixed(3)),
    render_commit_ms: Number((t2 - t1).toFixed(3)),
    first_paint_ms: Number((t3 - t0).toFixed(3)),
  };
}

function pointer(type: string, clientX: number, clientY: number, buttons: number): PointerEvent {
  return new PointerEvent(type, {
    bubbles: true,
    cancelable: true,
    pointerId: POINTER_ID,
    pointerType: "mouse",
    isPrimary: true,
    button: type === "pointermove" ? -1 : MIDDLE_BUTTON,
    buttons,
    clientX,
    clientY,
  });
}

/**
 * Pan and zoom route through the canvas inputs: per frame one wheel event at the canvas centre
 * (zoomAt keeps the centre) and one pointermove of a middle-button drag (pan), both flushed
 * synchronously so the camera of frame N is committed before frame N paints.
 */
async function runPanZoom({ durationMs, warmupMs }: Options) {
  const refresh_hz_probe = await probeRefresh();
  const svg = canvasElement();
  const rect = svg.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
  const fit = Math.min(width / WORLD_W, height / WORLD_H); // px per metre at zoom 1, as the spike
  const centre = { x: rect.left + width / 2, y: rect.top + height / 2 };
  let drag = { x: centre.x, y: centre.y };
  svg.dispatchEvent(pointer("pointerdown", drag.x, drag.y, MIDDLE_BUTTONS_MASK));

  function apply(t: number) {
    const target = cameraAt(t);
    const current = renderedCamera(svg);
    const factor = (fit * target.zoom) / current.scale;
    const deltaY = -Math.log(factor) / WHEEL_ZOOM_PER_PX;
    flushSync(() => {
      svg.dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true,
          cancelable: true,
          deltaMode: 0,
          deltaY,
          clientX: centre.x,
          clientY: centre.y,
        }),
      );
    });
    const zoomed = renderedCamera(svg);
    const dx = (zoomed.centerX - target.cx) * zoomed.scale;
    const dy = (target.cy - zoomed.centerY) * zoomed.scale;
    drag = { x: drag.x + dx, y: drag.y + dy };
    flushSync(() => {
      svg.dispatchEvent(pointer("pointermove", drag.x, drag.y, MIDDLE_BUTTONS_MASK));
    });
  }

  const deltas: number[] = [];
  const work: number[] = [];
  const errors: number[] = [];
  const centreErrors: number[] = [];
  let start: number | null = null;
  let last: number | null = null;
  for (;;) {
    const now = await nextFrame();
    if (start === null) start = now;
    const elapsed = now - start;
    if (elapsed >= warmupMs && last !== null) deltas.push(now - last);
    last = now;
    if (elapsed >= warmupMs + durationMs) break;
    const t = Math.max(0, elapsed - warmupMs);
    const before = performance.now();
    apply(t);
    if (elapsed >= warmupMs) {
      work.push(performance.now() - before);
      const target = cameraAt(t);
      const v = renderedCamera(svg);
      errors.push(Math.abs(v.scale / (fit * target.zoom) - 1));
      centreErrors.push(Math.hypot(v.centerX - target.cx, v.centerY - target.cy));
    }
  }
  svg.dispatchEvent(pointer("pointerup", drag.x, drag.y, 0));
  return {
    scenario: "pan-zoom",
    entities: entities.length,
    seed: SEED,
    viewport: { width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio },
    refresh_hz_probe,
    route_ms: durationMs,
    warmup_ms: warmupMs,
    work_per_frame_ms: summarizeWork(work),
    /** Camera reached vs route target: relative zoom error and centre error in metres. */
    max_zoom_tracking_error: Number(Math.max(0, ...errors).toExponential(2)),
    max_centre_tracking_error_m: Number(Math.max(0, ...centreErrors).toExponential(2)),
    ...summarize(deltas),
  };
}

/**
 * Edit scenario: per frame select one entity and commit one vertex move on it (store update,
 * history entry, queued operation and re-render), cycling through the polylines.
 */
async function runEdit({ durationMs, warmupMs }: Options) {
  const refresh_hz_probe = await probeRefresh();
  const polylines: EntityId[] = store
    .getState()
    .entities.order.filter((id) => store.getState().entities.byId[id]?.geometry.kind === "polyline");
  const deltas: number[] = [];
  const work: number[] = [];
  let start: number | null = null;
  let last: number | null = null;
  let step = 0;
  for (;;) {
    const now = await nextFrame();
    if (start === null) start = now;
    const elapsed = now - start;
    if (elapsed >= warmupMs && last !== null) deltas.push(now - last);
    last = now;
    if (elapsed >= warmupMs + durationMs) break;
    const id = polylines[step % polylines.length];
    if (id === undefined) throw new Error("No polylines in the document");
    const geometry = store.getState().entities.byId[id]?.geometry;
    if (geometry?.kind !== "polyline") throw new Error(`Entity ${id} is not a polyline`);
    const [x, y] = geometry.coords_m[0] ?? [0, 0];
    const offset = step % 2 === 0 ? 0.05 : -0.05;
    const before = performance.now();
    flushSync(() => {
      store.getState().select([id]);
      store.getState().moveVertex(id, 0, [x + offset, y + offset]);
    });
    if (elapsed >= warmupMs) work.push(performance.now() - before);
    step += 1;
  }
  return {
    scenario: "select-move-vertex",
    entities: entities.length,
    seed: SEED,
    viewport: { width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio },
    refresh_hz_probe,
    route_ms: durationMs,
    warmup_ms: warmupMs,
    edits: step,
    history_entries: store.getState().history.past.length,
    work_per_frame_ms: summarizeWork(work),
    ...summarize(deltas),
  };
}

const harness = window as unknown as Record<string, unknown>;
harness.__mountEditor = mountEditor;
harness.__runPanZoom = runPanZoom;
harness.__runEdit = runEdit;
harness.__spikeReady = true;
