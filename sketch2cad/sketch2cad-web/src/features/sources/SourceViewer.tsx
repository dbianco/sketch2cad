import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { TransformComponent, TransformWrapper, type ReactZoomPanPinchRef } from "react-zoom-pan-pinch";
import { VISUALLY_HIDDEN } from "../../editor/a11y/keyboardCursor";
import { PLACE_KEYS, cursorDelta } from "../../editor/a11y/shortcuts";

/** Longest side, in pixels, of the derived preview the viewer requests. */
export const PREVIEW_MAX_SIDE = 2048;

export const VIEWER_MESSAGES = {
  loading: "Cargando la vista previa…",
  denied: "No tienes permiso para ver esta fuente.",
  notFound: "La fuente no existe.",
  generic: "No se pudo cargar la vista previa. Recarga la página para reintentar.",
  markingKeys:
    "Con el teclado: con el visor enfocado, las flechas mueven el cursor (Mayús: paso fino; " +
    "Alt o Ctrl: paso largo) e Intro o Espacio marcan el punto.",
  cursorAt: (px: readonly [number, number]) =>
    `Cursor: x ${String(Math.round(px[0]))} px, y ${String(Math.round(px[1]))} px`,
  marked: (px: readonly [number, number]) =>
    `Punto marcado: x ${String(Math.round(px[0]))} px, y ${String(Math.round(px[1]))} px`,
} as const;

/**
 * Keyboard cursor step, as a fraction of the longest image side: 1 % (Shift: 0.1 %, Alt or
 * Ctrl: 10 %, see editor/a11y/shortcuts.ts). On an A4 scan at 300 DPI that is about 3 mm.
 */
export const MARKING_STEP_FRACTION = 0.01;

/**
 * URL of the derived JPEG preview of a source. The viewer never requests
 * `/sources/{id}/original`: the original is only downloaded explicitly.
 */
export function previewUrl(sourceId: string, maxSide: number = PREVIEW_MAX_SIDE): string {
  return `/api/v1/sources/${encodeURIComponent(sourceId)}/preview?max=${String(maxSide)}`;
}

type PreviewState =
  | { sourceId: string; kind: "ready"; objectUrl: string }
  | { sourceId: string; kind: "error"; message: string };

function errorMessage(status: number): string {
  if (status === 403) return VIEWER_MESSAGES.denied;
  if (status === 404) return VIEWER_MESSAGES.notFound;
  return VIEWER_MESSAGES.generic;
}

/**
 * Fetch the preview as a blob with the session cookie and expose it as an object URL,
 * revoked when the source changes or the viewer unmounts.
 */
function usePreview(sourceId: string): PreviewState | null {
  const [state, setState] = useState<PreviewState | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;

    async function load(): Promise<void> {
      try {
        const response = await fetch(previewUrl(sourceId), {
          credentials: "include",
          headers: { Accept: "image/jpeg" },
          signal: controller.signal,
        });
        if (!response.ok) {
          if (!controller.signal.aborted) {
            setState({ sourceId, kind: "error", message: errorMessage(response.status) });
          }
          return;
        }
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ sourceId, kind: "ready", objectUrl });
      } catch {
        if (!controller.signal.aborted) {
          setState({ sourceId, kind: "error", message: VIEWER_MESSAGES.generic });
        }
      }
    }

    void load();
    return () => {
      controller.abort();
      if (objectUrl !== null) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [sourceId]);

  return state?.sourceId === sourceId ? state : null;
}

const ZOOM_IN_KEYS: ReadonlySet<string> = new Set(["+", "="]);
const ZOOM_OUT_KEYS: ReadonlySet<string> = new Set(["-", "_"]);
const RESET_KEYS: ReadonlySet<string> = new Set(["0"]);

/** True when the key press belongs to a text field and must not drive the viewer. */
function isTypingTarget(target: EventTarget): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

const styles = {
  viewer: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    background: "#fff",
    border: "1px solid #D5DCE3",
    borderRadius: 8,
    padding: 8,
  },
  toolbar: { display: "flex", gap: 8, alignItems: "center" },
  button: {
    minWidth: 44,
    height: 36,
    padding: "0 12px",
    background: "#fff",
    color: "#14202B",
    border: "1px solid #7C8B99",
    borderRadius: 6,
    fontSize: 15,
    cursor: "pointer",
  },
  scale: { fontSize: 13, color: "#4A5966", marginLeft: "auto" },
  stage: { width: "100%", height: 420, background: "#E1E6EB", borderRadius: 4, overflow: "hidden" },
  image: { display: "block", maxWidth: "100%", maxHeight: 420, userSelect: "none" },
  message: { margin: 0, padding: 12, fontSize: 14 },
  alert: { margin: 0, padding: "8px 10px", color: "#8A1C1C", background: "#FDECEC", borderRadius: 4 },
  overlay: { position: "relative", display: "inline-block" },
  crosshair: {
    position: "absolute",
    width: 24,
    height: 24,
    transform: "translate(-50%, -50%)",
    border: "2px solid #0B57D0",
    borderRadius: "50%",
    boxShadow: "0 0 0 1px #fff",
    pointerEvents: "none",
  },
  marker: {
    position: "absolute",
    width: 18,
    height: 18,
    transform: "translate(-50%, -50%)",
    borderRadius: "50%",
    background: "#B3261E",
    border: "2px solid #fff",
    color: "#fff",
    fontSize: 11,
    fontWeight: 700,
    lineHeight: "18px",
    textAlign: "center",
    pointerEvents: "none",
  },
} satisfies Record<string, CSSProperties>;

/** Pointer travel, in screen pixels, above which a press is a pan and not a click. */
const CLICK_SLOP_PX = 4;

/** A point marked on the source, in natural pixels of the original image. */
export interface MarkedPoint {
  px: [number, number];
  label: string;
  color?: string;
}

/**
 * Point marking over the preview. Clicks are reported in natural pixels of the original
 * (`imageSize` is the source `width_px` x `height_px`), not of the downscaled preview.
 */
export interface SourceMarking {
  imageSize: { width: number; height: number };
  points: readonly MarkedPoint[];
  onPoint: (px: [number, number]) => void;
}

export interface SourceViewerProps {
  sourceId: string;
  alt: string;
  marking?: SourceMarking;
}

const round2 = (value: number) => Math.round(value * 100) / 100;
const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

/**
 * Pan and zoom viewer of the raster source (D-14: `react-zoom-pan-pinch` is used only here,
 * never for the SVG editor). Buttons and the `+`, `-` and `0` keys (while the viewer has
 * focus) zoom in, zoom out and reset. The current scale is exposed in `data-scale`. With
 * `marking`, a click (not a pan) on the image reports a point in original pixels and the
 * marked points are drawn over the image, zooming with it.
 */
export function SourceViewer({ sourceId, alt, marking }: SourceViewerProps) {
  const preview = usePreview(sourceId);
  const controlsRef = useRef<ReactZoomPanPinchRef | null>(null);
  const pressRef = useRef<{ x: number; y: number } | null>(null);
  const [scale, setScale] = useState(1);
  /** Keyboard cursor in original pixels; null until an arrow key is pressed. */
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [announcement, setAnnouncement] = useState("");

  function handleImageClick(event: MouseEvent<HTMLImageElement>): void {
    if (marking === undefined) return;
    const press = pressRef.current;
    if (press !== null && Math.hypot(event.clientX - press.x, event.clientY - press.y) > CLICK_SLOP_PX) {
      return; // the press panned the image
    }
    // The bounding box includes the zoom transform, so the fraction maps to the original.
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const { width, height } = marking.imageSize;
    marking.onPoint([
      round2(clamp01((event.clientX - rect.left) / rect.width) * width),
      round2(clamp01((event.clientY - rect.top) / rect.height) * height),
    ]);
  }

  const zoomIn = () => void controlsRef.current?.zoomIn(undefined, 0);
  const zoomOut = () => void controlsRef.current?.zoomOut(undefined, 0);
  const reset = () => void controlsRef.current?.resetTransform(0);

  /** Arrow keys and Enter/Space on the viewer itself drive the marking cursor. */
  function handleMarkingKey(event: KeyboardEvent<HTMLDivElement>): boolean {
    if (marking === undefined || preview?.kind !== "ready" || event.metaKey) return false;
    if (event.target !== event.currentTarget) return false;
    const { width, height } = marking.imageSize;
    const current = cursor ?? [width / 2, height / 2];
    const delta = cursorDelta(event, Math.max(width, height) * MARKING_STEP_FRACTION);
    if (delta !== null) {
      const next: [number, number] = [
        round2(Math.min(width, Math.max(0, current[0] + delta[0]))),
        round2(Math.min(height, Math.max(0, current[1] + delta[1]))),
      ];
      setCursor(next);
      setAnnouncement(VIEWER_MESSAGES.cursorAt(next));
      return true;
    }
    if (!PLACE_KEYS.has(event.key) || event.altKey || event.ctrlKey) return false;
    const point: [number, number] = [round2(current[0]), round2(current[1])];
    setCursor(point);
    marking.onPoint(point);
    setAnnouncement(VIEWER_MESSAGES.marked(point));
    return true;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (handleMarkingKey(event)) {
      event.preventDefault();
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey || isTypingTarget(event.target)) {
      return;
    }
    const action = ZOOM_IN_KEYS.has(event.key)
      ? zoomIn
      : ZOOM_OUT_KEYS.has(event.key)
        ? zoomOut
        : RESET_KEYS.has(event.key)
          ? reset
          : null;
    if (action !== null) {
      event.preventDefault();
      action();
    }
  }

  const ready = preview?.kind === "ready";

  return (
    <div
      role="region"
      aria-label={`Visor de la fuente: ${alt}`}
      aria-describedby={marking === undefined ? undefined : `${sourceId}-marking-keys`}
      tabIndex={0}
      data-scale={String(scale)}
      data-testid="source-viewer"
      style={styles.viewer}
      onKeyDown={handleKeyDown}
    >
      <div role="toolbar" aria-label="Zoom" style={styles.toolbar}>
        <button type="button" aria-label="Acercar" title="Acercar (+)" style={styles.button} disabled={!ready} onClick={zoomIn}>
          +
        </button>
        <button type="button" aria-label="Alejar" title="Alejar (−)" style={styles.button} disabled={!ready} onClick={zoomOut}>
          −
        </button>
        <button
          type="button"
          aria-label="Restablecer el zoom al 100 %"
          title="Restablecer el zoom (0)"
          style={styles.button}
          disabled={!ready}
          onClick={reset}
        >
          100 %
        </button>
        <span aria-live="polite" style={styles.scale}>
          {Math.round(scale * 100)} %
        </span>
      </div>
      {marking !== undefined && (
        <p id={`${sourceId}-marking-keys`} style={styles.message}>
          {VIEWER_MESSAGES.markingKeys}
        </p>
      )}
      {marking !== undefined && (
        <div aria-live="polite" aria-atomic="true" style={VISUALLY_HIDDEN}>
          {announcement}
        </div>
      )}
      {preview === null && (
        <p role="status" style={styles.message}>
          {VIEWER_MESSAGES.loading}
        </p>
      )}
      {preview?.kind === "error" && (
        <p role="alert" style={styles.alert}>
          {preview.message}
        </p>
      )}
      {preview?.kind === "ready" && (
        <TransformWrapper
          ref={controlsRef}
          centerOnInit
          onTransform={(_ref, state) => setScale(state.scale)}
        >
          <TransformComponent wrapperStyle={styles.stage}>
            <div style={styles.overlay}>
              <img
                src={preview.objectUrl}
                alt={alt}
                draggable={false}
                style={marking === undefined ? styles.image : { ...styles.image, cursor: "crosshair" }}
                onMouseDown={(event) => {
                  pressRef.current = { x: event.clientX, y: event.clientY };
                }}
                onClick={handleImageClick}
              />
              {marking?.points.map((point, index) => (
                <span
                  key={index}
                  aria-hidden="true"
                  data-testid="source-marker"
                  style={{
                    ...styles.marker,
                    background: point.color ?? styles.marker.background,
                    left: `${String((point.px[0] / marking.imageSize.width) * 100)}%`,
                    top: `${String((point.px[1] / marking.imageSize.height) * 100)}%`,
                  }}
                >
                  {point.label}
                </span>
              ))}
              {marking !== undefined && cursor !== null && (
                <span
                  aria-hidden="true"
                  data-testid="source-cursor"
                  style={{
                    ...styles.crosshair,
                    left: `${String((cursor[0] / marking.imageSize.width) * 100)}%`,
                    top: `${String((cursor[1] / marking.imageSize.height) * 100)}%`,
                  }}
                />
              )}
            </div>
          </TransformComponent>
        </TransformWrapper>
      )}
    </div>
  );
}
