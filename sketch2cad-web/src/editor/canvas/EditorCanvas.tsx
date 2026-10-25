// SVG editor canvas (jotdown H2, decisions D-2 and D-14): entities grouped by layer over the
// source image. Pan and zoom only rewrite the `<svg>` viewBox (viewport.ts); no wrapper ever gets
// a CSS transform, which would break `vector-effect: non-scaling-stroke`. Hit-testing uses
// native SVG pointer events on the entity elements.
//
// Paper sizes of texts and dimension text become world metres with the plan's paper scale:
// `m = mm * paperScale / 1000` (spec 13). The canvas then shows them as the DXF and PDF will,
// growing and shrinking with the zoom, and zooming never has to re-render them. Plans without a
// declared scale use the provisional 1:100 of spec section 10. Symbols follow their `size_mode`
// (symbols.ts): `world_m` ones scale with the zoom, `paper_mm` ones (annotative) keep a constant
// screen size, so only symbol elements re-render on zoom (ZoomContext).
import {
  createContext,
  memo,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
  type ReactNode,
  type SVGProps,
} from "react";
import type { StoreApi } from "zustand/vanilla";
import { useStore } from "zustand";
import type { components } from "../../api/schema";
import { editorStore, selectVisibleIds, type EditorStore } from "../store/entities";
import type { EditorEntity, EntityId, Geometry, Point2 } from "../store/types";
import { CURSOR_TEXT, VISUALLY_HIDDEN, useKeyboardCursor } from "../a11y/keyboardCursor";
import {
  groupByLayer,
  layerFlags,
  layerViews,
  sameIds,
  toggleLock,
  toggleVisibility,
  type LayerFlags,
  type LayerFlagsByName,
  type LayerView,
} from "./layers";
import { SYMBOL_UNITS_PER_BLOCK, parseSymbolSvg } from "./symbolArt";
import { symbolRenderSize, symbolSizeInput, type CatalogSymbol } from "./symbols";
import {
  boundsOf,
  fitToBounds,
  pan,
  screenToWorld,
  viewBoxAttribute,
  zoomAt,
  type Viewport,
  type WorldRect,
} from "./viewport";

export type Catalog = components["schemas"]["CatalogOut"];

/** Paper scale denominator used when the plan has none (provisional 1:100, spec 10). */
export const DEFAULT_PAPER_SCALE = 100;
/** Screen width of entity strokes, in px (non-scaling). */
export const STROKE_WIDTH_PX = 1.5;
/** Screen diameter of a point entity, in px. */
export const POINT_SIZE_PX = 7;
/** Paper height of a dimension's text, in mm. */
export const DIMENSION_TEXT_MM = 2.5;
/** Color of selected entities. */
export const SELECTION_COLOR = "#ff7a00";
/** Zoom factor per wheel pixel: `factor = exp(-deltaY * WHEEL_ZOOM_PER_PX)`. */
export const WHEEL_ZOOM_PER_PX = 0.0015;
/** Keyboard steps: arrow keys pan by KEY_PAN_PX, + and - zoom by KEY_ZOOM_FACTOR. */
export const KEY_PAN_PX = 50;
export const KEY_ZOOM_FACTOR = 1.25;
export const DEFAULT_IMAGE_OPACITY = 0.5;
/** World rectangle shown when there are no entities and no image. */
const EMPTY_BOUNDS: WorldRect = { minX: 0, minY: 0, maxX: 10, maxY: 10 };
const LINE_DELTA_PX = 16;

export const CANVAS_TEXT = {
  canvas: "Lienzo del plano",
  opacity: "Opacidad de la imagen de origen",
  opacityShort: "Opacidad",
  layers: "Capas",
  show: (layer: string) => `Mostrar capa ${layer}`,
  lock: (layer: string) => `Bloquear capa ${layer}`,
  locked: "Bloqueada",
  fit: "Encuadrar el plano",
} as const;

export interface EditorCanvasProps {
  catalog: Catalog;
  /** Store to render; the editor's singleton by default. */
  store?: StoreApi<EditorStore>;
  /** Fixed canvas size in px; without it the canvas fills its container (ResizeObserver). */
  width?: number;
  height?: number;
  /** Denominator of the plan's paper scale (100 for 1:100). */
  paperScale?: number;
  /** Source image drawn beneath the entities, and the world rectangle it covers. */
  imageUrl?: string;
  imageBounds?: WorldRect;
  /** Called with the camera after every change, for tools that convert pointer coordinates. */
  onViewportChange?: (viewport: Viewport) => void;
  /**
   * Active editor tool. Without one the canvas behaves as a viewer: clicking an entity selects
   * it and a background drag pans. With one, primary-button presses go to the tool first and
   * pan only when it declines them; the middle button and Space + drag always pan.
   */
  tool?: CanvasTool;
  /** Controlled layer flags; without them the layer panel keeps its own state. */
  layerFlags?: LayerFlagsByName;
  onLayerFlagsChange?: (flags: LayerFlagsByName) => void;
  /** HTML laid over the canvas at fixed screen positions (e.g. the north arrow). */
  overlay?: ReactNode;
  /**
   * Keyboard cursor (a11y/keyboardCursor.tsx) for the active tool, with its arrow step in world
   * metres; without it the arrow keys pan the view.
   */
  keyboardCursor?: { step: number };
}

/** A pointer event as tools receive it. */
export interface CanvasPointer {
  /** World point (m, Y up) under the pointer. */
  world: Point2;
  /** Screen pixels per world metre at the time of the event. */
  scale: number;
  /** Entity under the pointer, if any. */
  entityId: EntityId | null;
  shiftKey: boolean;
}

/** Handlers of an editor tool; see `EditorCanvasProps.tool`. */
export interface CanvasTool {
  /** Primary-button press; return true to take the pointer (no pan) until it is released. */
  onPointerDown?(pointer: CanvasPointer): boolean;
  /** Pointer moves, pressed or hovering. */
  onPointerMove?(pointer: CanvasPointer): void;
  /** Release of a pointer the tool took. */
  onPointerUp?(pointer: CanvasPointer): void;
  onDoubleClick?(pointer: CanvasPointer): void;
  /** SVG drawn above the entities, in SVG user units (metres, Y down; see `svgPoints`). */
  preview?: ReactNode;
  /** HTML laid over the canvas, positioned in screen pixels of `viewport`. */
  renderOverlay?(viewport: Viewport): ReactNode;
  /** Whether the tool handles `key` itself right now, so the keyboard cursor must leave it. */
  ownsKey?(key: string): boolean;
  /** The tool marks by dragging: the keyboard cursor presses on one Enter, releases on the next. */
  keyboardDrag?: boolean;
}

type Camera = Pick<Viewport, "centerX" | "centerY" | "scale">;

interface SymbolArt {
  children: ReactElement[] | null;
  symbol: CatalogSymbol;
}

interface CanvasContextValue {
  store: StoreApi<EditorStore>;
  /** True when a tool handles presses: entity elements then let them reach the canvas. */
  delegate: boolean;
  symbols: ReadonlyMap<string, SymbolArt>;
  paperScale: number;
}

const CanvasContext = createContext<CanvasContextValue | null>(null);

function useCanvasContext(): CanvasContextValue {
  const value = useContext(CanvasContext);
  if (value === null) throw new Error("Entity elements must be rendered inside EditorCanvas");
  return value;
}

/** Zoom (px per metre) of the canvas, read only by symbol elements. */
const ZoomContext = createContext(1);

export type SymbolGlyphProps = Omit<SVGProps<SVGGElement>, "transform" | "children"> & {
  symbol: CatalogSymbol;
  /** Shapes parsed from the catalog SVG (`parseSymbolSvg`). */
  art: readonly ReactElement[];
  geometry: Pick<Extract<Geometry, { kind: "symbol" }>, "anchor_m" | "rotation_deg" | "scale">;
  paperScale: number;
};

/** A catalog symbol at its anchor, sized for the current zoom by its `size_mode`. */
export function SymbolGlyph({ symbol, art, geometry, paperScale, ...rest }: SymbolGlyphProps) {
  const zoom = useContext(ZoomContext);
  const input = symbolSizeInput(symbol, geometry.scale, paperScale);
  // SVG user units (metres) per catalog SVG unit.
  const unit = symbolRenderSize(input, { scale: zoom }) / zoom / SYMBOL_UNITS_PER_BLOCK;
  const transform =
    `translate(${String(svgX(geometry.anchor_m))} ${String(svgY(geometry.anchor_m))}) ` +
    `rotate(${String(-geometry.rotation_deg)}) scale(${String(unit)})`;
  return (
    <g {...rest} transform={transform}>
      {art}
    </g>
  );
}

/** SVG coordinates (Y down) of a world point. */
function svgX(point: Point2): number {
  return point[0];
}
function svgY(point: Point2): number {
  return -point[1];
}
/** SVG `points` attribute of world points. */
export function svgPoints(points: readonly Point2[]): string {
  return points.map((p) => `${String(svgX(p))},${String(svgY(p))}`).join(" ");
}

/** Dimension value in metres with a decimal comma, as the UI shows numbers. */
export function formatMetres(value: number): string {
  return `${value.toFixed(2).replace(".", ",")} m`;
}

function geometryPoints(geometry: Geometry): Point2[] {
  switch (geometry.kind) {
    case "line":
    case "polyline":
    case "point":
      return geometry.coords_m;
    case "text":
    case "symbol":
      return [geometry.anchor_m];
    case "dimension":
      return [geometry.p1_m, geometry.p2_m];
  }
}

interface ShapeProps {
  "data-entity-id": string;
  "data-kind": string;
  color: string | undefined;
  onPointerDown: (event: PointerEvent<SVGElement>) => void;
}

function renderGeometry(
  geometry: Geometry,
  common: ShapeProps,
  context: CanvasContextValue,
): ReactElement {
  const nonScaling = "non-scaling-stroke";
  switch (geometry.kind) {
    case "line":
      return <polyline {...common} points={svgPoints(geometry.coords_m)} vectorEffect={nonScaling} />;
    case "polyline":
      return geometry.closed ? (
        <polygon {...common} points={svgPoints(geometry.coords_m)} vectorEffect={nonScaling} />
      ) : (
        <polyline {...common} points={svgPoints(geometry.coords_m)} vectorEffect={nonScaling} />
      );
    case "point":
      // A zero-length subpath with round caps paints a dot of constant screen size.
      return (
        <path
          {...common}
          d={geometry.coords_m.map((p) => `M${String(svgX(p))} ${String(svgY(p))}h0`).join("")}
          strokeLinecap="round"
          strokeWidth={POINT_SIZE_PX}
          vectorEffect={nonScaling}
        />
      );
    case "text": {
      const x = svgX(geometry.anchor_m);
      const y = svgY(geometry.anchor_m);
      return (
        <text
          {...common}
          x={x}
          y={y}
          fontSize={(geometry.height_mm_paper * context.paperScale) / 1000}
          transform={`rotate(${String(-geometry.rotation_deg)} ${String(x)} ${String(y)})`}
          fill="currentColor"
          stroke="none"
        >
          {geometry.content}
        </text>
      );
    }
    case "dimension":
      return renderDimension(geometry, common, context.paperScale);
    case "symbol": {
      const art = context.symbols.get(geometry.symbol_id);
      if (art?.children == null) {
        // Unknown or rejected symbol: a dot keeps it visible and selectable.
        return renderGeometry({ kind: "point", coords_m: [geometry.anchor_m] }, common, context);
      }
      return (
        <SymbolGlyph
          {...common}
          data-symbol-id={geometry.symbol_id}
          symbol={art.symbol}
          art={art.children}
          geometry={geometry}
          paperScale={context.paperScale}
        />
      );
    }
  }
}

function renderDimension(
  geometry: Extract<Geometry, { kind: "dimension" }>,
  common: ShapeProps,
  paperScale: number,
): ReactElement {
  const [x1, y1] = geometry.p1_m;
  const [x2, y2] = geometry.p2_m;
  const length = Math.hypot(x2 - x1, y2 - y1);
  // Unit normal to the left of p1 -> p2 (world, Y up); the dimension line is offset along it.
  const nx = length > 0 ? -(y2 - y1) / length : 0;
  const ny = length > 0 ? (x2 - x1) / length : 0;
  const off = geometry.offset_m;
  const q1: Point2 = [x1 + nx * off, y1 + ny * off];
  const q2: Point2 = [x2 + nx * off, y2 + ny * off];
  const value = geometry.declared_value_m ?? geometry.measured_value_m ?? length;
  let angle = (Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI;
  if (angle > 90 || angle <= -90) angle -= Math.sign(angle) * 180;
  const textHeight = (DIMENSION_TEXT_MM * paperScale) / 1000;
  const mid: Point2 = [(q1[0] + q2[0]) / 2, (q1[1] + q2[1]) / 2];
  return (
    <g {...common}>
      {off !== 0 && (
        <path
          d={`M${svgPoints([geometry.p1_m, q1])}M${svgPoints([geometry.p2_m, q2])}`}
          vectorEffect="non-scaling-stroke"
        />
      )}
      <polyline points={svgPoints([q1, q2])} vectorEffect="non-scaling-stroke" />
      <text
        transform={`translate(${String(svgX(mid))} ${String(svgY(mid))}) rotate(${String(-angle)})`}
        y={-textHeight / 3}
        fontSize={textHeight}
        textAnchor="middle"
        fill="currentColor"
        stroke="none"
      >
        {formatMetres(value)}
      </text>
    </g>
  );
}

/** One entity; re-renders only when its own entity or selection state changes. */
const EntityElement = memo(function EntityElement({ id }: { id: EntityId }) {
  const context = useCanvasContext();
  const { store } = context;
  const entity: EditorEntity | undefined = useStore(store, (state) => state.entities.byId[id]);
  const selected = useStore(store, (state) => state.selection.includes(id));
  if (entity === undefined || entity.deleted) return null;
  const common: ShapeProps = {
    "data-entity-id": id,
    "data-kind": entity.geometry.kind,
    color: selected ? SELECTION_COLOR : undefined,
    onPointerDown: (event) => {
      if (event.button !== 0 || context.delegate) return;
      event.stopPropagation();
      store.getState().select([id]);
    },
  };
  return renderGeometry(entity.geometry, common, context);
});

interface LayerGroupProps {
  layer: LayerView;
  ids: readonly EntityId[];
  flags: LayerFlags;
}

/** The entities of one layer; skipped on viewport and selection changes. */
const LayerGroup = memo(
  function LayerGroup({ layer, ids, flags }: LayerGroupProps) {
    return (
      <g
        data-layer={layer.name}
        color={layer.color}
        stroke="currentColor"
        fill="none"
        strokeWidth={STROKE_WIDTH_PX}
        display={flags.visible ? undefined : "none"}
        pointerEvents={flags.locked ? "none" : undefined}
      >
        {flags.visible && ids.map((id) => <EntityElement key={id} id={id} />)}
      </g>
    );
  },
  (a, b) =>
    a.layer.name === b.layer.name &&
    a.layer.color === b.layer.color &&
    a.flags.visible === b.flags.visible &&
    a.flags.locked === b.flags.locked &&
    sameIds(a.ids, b.ids),
);

function useContainerSize(fixedWidth?: number, fixedHeight?: number) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState({ width: 0, height: 0 });
  const fixed = fixedWidth !== undefined && fixedHeight !== undefined;
  useEffect(() => {
    const element = containerRef.current;
    if (fixed || element === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry === undefined) return;
      const { width, height } = entry.contentRect;
      setMeasured({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [fixed]);
  return {
    containerRef,
    width: fixedWidth ?? measured.width,
    height: fixedHeight ?? measured.height,
  };
}

function LayerPanel({
  layers,
  flags,
  onToggleVisible,
  onToggleLock,
}: {
  layers: readonly LayerView[];
  flags: LayerFlagsByName;
  onToggleVisible: (name: string) => void;
  onToggleLock: (name: string) => void;
}) {
  return (
    <fieldset className="editor-layer-panel">
      <legend>{CANVAS_TEXT.layers}</legend>
      {layers.map((layer) => {
        const state = layerFlags(flags, layer.name);
        return (
          <div key={layer.name} title={layer.description}>
            <span aria-hidden="true" style={{ color: layer.color }}>
              ■
            </span>{" "}
            <label>
              <input
                type="checkbox"
                checked={state.visible}
                aria-label={CANVAS_TEXT.show(layer.name)}
                onChange={() => onToggleVisible(layer.name)}
              />
              {layer.name}
            </label>{" "}
            <label>
              <input
                type="checkbox"
                checked={state.locked}
                aria-label={CANVAS_TEXT.lock(layer.name)}
                onChange={() => onToggleLock(layer.name)}
              />
              {CANVAS_TEXT.locked}
            </label>
          </div>
        );
      })}
    </fieldset>
  );
}

export function EditorCanvas({
  catalog,
  store = editorStore,
  width: fixedWidth,
  height: fixedHeight,
  paperScale = DEFAULT_PAPER_SCALE,
  imageUrl,
  imageBounds,
  onViewportChange,
  tool,
  layerFlags: controlledFlags,
  onLayerFlagsChange,
  overlay,
  keyboardCursor,
}: EditorCanvasProps) {
  const { containerRef, width, height } = useContainerSize(fixedWidth, fixedHeight);
  const svgRef = useRef<SVGSVGElement>(null);
  const ids = useStore(store, selectVisibleIds);
  const byId = useStore(store, (state) => state.entities.byId);
  const [localFlags, setLocalFlags] = useState<LayerFlagsByName>({});
  const flags = controlledFlags ?? localFlags;
  function setFlags(update: (current: LayerFlagsByName) => LayerFlagsByName) {
    const next = update(flags);
    if (controlledFlags === undefined) setLocalFlags(next);
    onLayerFlagsChange?.(next);
  }
  const [opacity, setOpacity] = useState(DEFAULT_IMAGE_OPACITY);
  /** `null` until the user moves the camera: meanwhile the canvas fits the content. */
  const [camera, setCamera] = useState<Camera | null>(null);

  const groups = useMemo(() => groupByLayer(ids, byId), [ids, byId]);
  const layers = useMemo(() => layerViews(catalog.layers, groups.keys()), [catalog.layers, groups]);
  const delegate = tool !== undefined;
  const context = useMemo<CanvasContextValue>(
    () => ({
      store,
      delegate,
      paperScale,
      symbols: new Map(
        catalog.symbols.map((symbol) => [
          symbol.symbol_id,
          { children: parseSymbolSvg(symbol.svg), symbol },
        ]),
      ),
    }),
    [store, delegate, paperScale, catalog.symbols],
  );

  const fitted = useMemo(() => {
    const points = ids.flatMap((id) => {
      const entity = byId[id];
      return entity === undefined ? [] : geometryPoints(entity.geometry);
    });
    if (imageUrl !== undefined && imageBounds !== undefined) {
      points.push([imageBounds.minX, imageBounds.minY], [imageBounds.maxX, imageBounds.maxY]);
    }
    return fitToBounds(boundsOf(points) ?? EMPTY_BOUNDS, width, height);
  }, [ids, byId, imageUrl, imageBounds, width, height]);

  const viewport: Viewport = camera === null ? fitted : { ...camera, width, height };

  // Latest viewport for the native wheel listener, which is attached once.
  const viewportRef = useRef(viewport);
  useEffect(() => {
    viewportRef.current = viewport;
    onViewportChange?.(viewport);
  });

  const cursor = useKeyboardCursor({
    tool,
    step: keyboardCursor?.step,
    viewport,
    onPan: (next) => apply(next),
  });

  const pointers = useRef(new Map<number, Point2>());
  /** Pointers taken by the tool; they never pan. */
  const toolPointers = useRef(new Set<number>());
  const spaceHeld = useRef(false);

  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === " ") spaceHeld.current = event.type === "keydown";
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
    };
  }, []);

  function canvasPointer(event: PointerEvent<SVGSVGElement> | MouseEvent<SVGSVGElement>) {
    const svg = event.currentTarget;
    const target = event.target instanceof Element ? event.target : null;
    const hit = target?.closest("[data-entity-id]") ?? null;
    const pointer: CanvasPointer = {
      world: clientToWorld(svg, viewportRef.current, event.clientX, event.clientY),
      scale: viewportRef.current.scale,
      entityId: hit !== null && svg.contains(hit) ? hit.getAttribute("data-entity-id") : null,
      shiftKey: event.shiftKey,
    };
    return pointer;
  }

  function screenPoint(clientX: number, clientY: number): Point2 {
    const rect = svgRef.current?.getBoundingClientRect();
    return [clientX - (rect?.left ?? 0), clientY - (rect?.top ?? 0)];
  }

  function apply(next: Viewport) {
    viewportRef.current = next;
    setCamera({ centerX: next.centerX, centerY: next.centerY, scale: next.scale });
  }

  useEffect(() => {
    const svg = svgRef.current;
    if (svg === null) return;
    function onWheel(event: WheelEvent) {
      event.preventDefault();
      const delta = event.deltaMode === 0 ? event.deltaY : event.deltaY * LINE_DELTA_PX;
      const rect = svg?.getBoundingClientRect();
      const point: Point2 = [event.clientX - (rect?.left ?? 0), event.clientY - (rect?.top ?? 0)];
      const next = zoomAt(viewportRef.current, point, Math.exp(-delta * WHEEL_ZOOM_PER_PX));
      viewportRef.current = next;
      setCamera({ centerX: next.centerX, centerY: next.centerY, scale: next.scale });
    }
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  function onPointerDown(event: PointerEvent<SVGSVGElement>) {
    // Entity elements stop the primary button, so this is a drag on the background, or a
    // middle-button drag anywhere.
    if (event.button !== 0 && event.button !== 1) return;
    const toolTakes =
      tool?.onPointerDown !== undefined &&
      event.button === 0 &&
      !spaceHeld.current &&
      tool.onPointerDown(canvasPointer(event));
    if (toolTakes) {
      toolPointers.current.add(event.pointerId);
      event.currentTarget.setPointerCapture?.(event.pointerId);
      return;
    }
    pointers.current.set(event.pointerId, screenPoint(event.clientX, event.clientY));
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function onPointerMove(event: PointerEvent<SVGSVGElement>) {
    if (tool !== undefined && !pointers.current.has(event.pointerId)) {
      const pointer = canvasPointer(event);
      cursor.notePointer(pointer.world);
      tool.onPointerMove?.(pointer);
      return;
    }
    const previous = pointers.current.get(event.pointerId);
    if (previous === undefined) return;
    const point = screenPoint(event.clientX, event.clientY);
    const other = [...pointers.current].find(([id]) => id !== event.pointerId)?.[1];
    pointers.current.set(event.pointerId, point);
    const current = viewportRef.current;
    if (other === undefined) {
      apply(pan(current, point[0] - previous[0], point[1] - previous[1]));
      return;
    }
    // Pinch: zoom around the midpoint by the change of distance, then follow the midpoint.
    const before = Math.hypot(previous[0] - other[0], previous[1] - other[1]);
    const after = Math.hypot(point[0] - other[0], point[1] - other[1]);
    const midBefore: Point2 = [(previous[0] + other[0]) / 2, (previous[1] + other[1]) / 2];
    const midAfter: Point2 = [(point[0] + other[0]) / 2, (point[1] + other[1]) / 2];
    const zoomed = before > 0 ? zoomAt(current, midBefore, after / before) : current;
    apply(pan(zoomed, midAfter[0] - midBefore[0], midAfter[1] - midBefore[1]));
  }

  function onPointerEnd(event: PointerEvent<SVGSVGElement>) {
    pointers.current.delete(event.pointerId);
    if (toolPointers.current.delete(event.pointerId) && event.type === "pointerup") {
      tool?.onPointerUp?.(canvasPointer(event));
    }
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (cursor.onKeyDown(event)) return;
    const centre: Point2 = [width / 2, height / 2];
    const steps: Record<string, () => Viewport> = {
      ArrowLeft: () => pan(viewport, KEY_PAN_PX, 0),
      ArrowRight: () => pan(viewport, -KEY_PAN_PX, 0),
      ArrowUp: () => pan(viewport, 0, KEY_PAN_PX),
      ArrowDown: () => pan(viewport, 0, -KEY_PAN_PX),
      "+": () => zoomAt(viewport, centre, KEY_ZOOM_FACTOR),
      "=": () => zoomAt(viewport, centre, KEY_ZOOM_FACTOR),
      "-": () => zoomAt(viewport, centre, 1 / KEY_ZOOM_FACTOR),
    };
    const step = steps[event.key];
    if (step === undefined) return;
    event.preventDefault();
    apply(step());
  }

  const showImage = imageUrl !== undefined && imageBounds !== undefined;

  return (
    <div className="editor-canvas">
      <div
        ref={containerRef}
        className="editor-canvas-viewport"
        style={
          fixedWidth === undefined
            ? { position: "relative", width: "100%", height: "100%" }
            : { position: "relative" }
        }
      >
        <svg
          ref={svgRef}
          data-testid="editor-canvas"
          role="application"
          aria-label={CANVAS_TEXT.canvas}
          aria-describedby={keyboardCursor === undefined ? undefined : "editor-canvas-keys"}
          tabIndex={0}
          width={width}
          height={height}
          viewBox={viewBoxAttribute(viewport)}
          style={{ display: "block", touchAction: "none" }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd}
          onPointerCancel={onPointerEnd}
          onDoubleClick={(event) => tool?.onDoubleClick?.(canvasPointer(event))}
          onKeyDown={onKeyDown}
          onBlur={cursor.onBlur}
        >
          {showImage && (
            <image
              data-testid="source-image"
              href={imageUrl}
              x={imageBounds.minX}
              y={-imageBounds.maxY}
              width={imageBounds.maxX - imageBounds.minX}
              height={imageBounds.maxY - imageBounds.minY}
              preserveAspectRatio="none"
              opacity={opacity}
              pointerEvents="none"
            />
          )}
          <ZoomContext.Provider value={viewport.scale}>
            <CanvasContext.Provider value={context}>
              {layers.map((layer) => (
                <LayerGroup
                  key={layer.name}
                  layer={layer}
                  ids={groups.get(layer.name) ?? NO_IDS}
                  flags={layerFlags(flags, layer.name)}
                />
              ))}
            </CanvasContext.Provider>
            {tool?.preview !== undefined && <g pointerEvents="none">{tool.preview}</g>}
            {cursor.crosshair}
          </ZoomContext.Provider>
        </svg>
        {keyboardCursor !== undefined && (
          <p id="editor-canvas-keys" style={VISUALLY_HIDDEN}>
            {CURSOR_TEXT.hint}
          </p>
        )}
        <div aria-live="polite" aria-atomic="true" style={VISUALLY_HIDDEN}>
          {cursor.announcement}
        </div>
        {tool?.renderOverlay?.(viewport)}
        {overlay}
      </div>
      <div className="editor-canvas-controls">
        {showImage && (
          <label>
            {CANVAS_TEXT.opacityShort}{" "}
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={opacity}
              aria-label={CANVAS_TEXT.opacity}
              onChange={(event) => setOpacity(Number(event.target.value))}
            />
          </label>
        )}
        <button type="button" onClick={() => setCamera(null)}>
          {CANVAS_TEXT.fit}
        </button>
        <LayerPanel
          layers={layers}
          flags={flags}
          onToggleVisible={(name) => setFlags((current) => toggleVisibility(current, name))}
          onToggleLock={(name) => setFlags((current) => toggleLock(current, name))}
        />
      </div>
    </div>
  );
}

const NO_IDS: readonly EntityId[] = [];

/** World point under a client (page) position, for tools of tasks 061+. */
export function clientToWorld(
  svg: SVGSVGElement,
  viewport: Viewport,
  clientX: number,
  clientY: number,
): Point2 {
  const rect = svg.getBoundingClientRect();
  return screenToWorld(viewport, [clientX - rect.left, clientY - rect.top]);
}
