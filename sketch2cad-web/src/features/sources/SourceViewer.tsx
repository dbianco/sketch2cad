import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { TransformComponent, TransformWrapper, type ReactZoomPanPinchRef } from "react-zoom-pan-pinch";

/** Longest side, in pixels, of the derived preview the viewer requests. */
export const PREVIEW_MAX_SIDE = 2048;

export const VIEWER_MESSAGES = {
  loading: "Cargando la vista previa…",
  denied: "No tienes permiso para ver esta fuente.",
  notFound: "La fuente no existe.",
  generic: "No se pudo cargar la vista previa. Recarga la página para reintentar.",
} as const;

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
} satisfies Record<string, CSSProperties>;

export interface SourceViewerProps {
  sourceId: string;
  alt: string;
}

/**
 * Pan and zoom viewer of the raster source (D-14: `react-zoom-pan-pinch` is used only here,
 * never for the SVG editor). Buttons and the `+`, `-` and `0` keys (while the viewer has
 * focus) zoom in, zoom out and reset. The current scale is exposed in `data-scale`.
 */
export function SourceViewer({ sourceId, alt }: SourceViewerProps) {
  const preview = usePreview(sourceId);
  const controlsRef = useRef<ReactZoomPanPinchRef | null>(null);
  const [scale, setScale] = useState(1);

  const zoomIn = () => void controlsRef.current?.zoomIn(undefined, 0);
  const zoomOut = () => void controlsRef.current?.zoomOut(undefined, 0);
  const reset = () => void controlsRef.current?.resetTransform(0);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
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
          aria-label="Restablecer el zoom"
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
            <img src={preview.objectUrl} alt={alt} draggable={false} style={styles.image} />
          </TransformComponent>
        </TransformWrapper>
      )}
    </div>
  );
}
