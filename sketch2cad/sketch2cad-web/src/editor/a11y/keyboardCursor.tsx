// Keyboard cursor of the editor canvas (spec 5 WCAG 2.1 AA, jotdown H2-14). While the canvas has
// focus and a drawing tool is active, the arrow keys move a virtual cursor (a crosshair, with its
// local coordinates announced in a polite live region) by the grid step, and Enter or Space act
// as a primary click at it: the active tool receives the same CanvasPointer events as for a
// mouse click there (press then release), so every tool works unchanged. Tools that mark by
// dragging (`keyboardDrag`, the local frame) get the press on the first Enter, moves while the
// cursor travels, and the release on the second Enter. A key the tool claims (`ownsKey`, Enter
// of a polyline with 2+ vertices) is left to the tool. The cursor starts at the last pointer
// position or at the viewport centre, and follows the viewport (panning) when it leaves it.
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import type { CanvasPointer, CanvasTool } from "../canvas/EditorCanvas";
import { pan, worldToScreen, type Viewport } from "../canvas/viewport";
import type { Point2 } from "../store/types";
import { PLACE_KEYS, cursorDelta } from "./shortcuts";

/** Screen margin, in px, that the cursor keeps from the canvas edges (the view pans). */
export const CURSOR_EDGE_PX = 24;
/** Half length, in px, of the crosshair arms. */
const CROSSHAIR_PX = 12;
const CROSSHAIR_COLOR = "#0B57D0";

/** Metres with two decimals and a decimal comma (as `formatMetres` of the canvas). */
function metres(value: number): string {
  return `${value.toFixed(2).replace(".", ",")} m`;
}

export const CURSOR_TEXT = {
  hint:
    "Con una herramienta de dibujo: flechas para mover el cursor, Intro o Espacio para marcar " +
    "el punto, Esc para cancelar. Pulsa ? para ver todos los atajos.",
  at: (point: Point2) => `Cursor: X ${metres(point[0])}; Y ${metres(point[1])}`,
  placed: (point: Point2) =>
    `Punto marcado: X ${metres(point[0])}; Y ${metres(point[1])}`,
} as const;

/** Visually hidden but read by screen readers. */
export const VISUALLY_HIDDEN: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  margin: -1,
  padding: 0,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};

export interface KeyboardCursorOptions {
  tool: CanvasTool | undefined;
  /** Cursor step in world metres; `undefined` turns the cursor off. */
  step: number | undefined;
  viewport: Viewport;
  /** Move the camera (used to keep the cursor in view). */
  onPan(next: Viewport): void;
}

export interface KeyboardCursor {
  /** Handle a canvas key; returns true when it was used. */
  onKeyDown(event: KeyboardEvent<Element>): boolean;
  /** Remember the pointer position (and hide the crosshair: the mouse is in use). */
  notePointer(world: Point2): void;
  onBlur(): void;
  /** Crosshair, in SVG user units (metres, Y down), or null when hidden. */
  crosshair: ReactNode;
  /** Text of the live region. */
  announcement: string;
}

/** Cursor position after an arrow key: screen y grows down, world y grows up. */
export function movedCursor(point: Point2, delta: [number, number]): Point2 {
  return [point[0] + delta[0], point[1] - delta[1]];
}

/** Viewport panned just enough for `point` to be CURSOR_EDGE_PX inside it. */
export function followCursor(viewport: Viewport, point: Point2): Viewport {
  const [x, y] = worldToScreen(viewport, point);
  const margin = Math.min(CURSOR_EDGE_PX, viewport.width / 2, viewport.height / 2);
  const dx = x < margin ? margin - x : x > viewport.width - margin ? viewport.width - margin - x : 0;
  const dy =
    y < margin ? margin - y : y > viewport.height - margin ? viewport.height - margin - y : 0;
  return dx === 0 && dy === 0 ? viewport : pan(viewport, dx, dy);
}

/**
 * A key the cursor used: no default action, and it does not reach the editor's document-level
 * listeners (the tool keys of EditorWorkspace: Enter would also finish a polyline).
 */
function consume(event: KeyboardEvent<Element>): void {
  event.preventDefault();
  event.stopPropagation();
}

export function useKeyboardCursor({
  tool,
  step,
  viewport,
  onPan,
}: KeyboardCursorOptions): KeyboardCursor {
  const [position, setPosition] = useState<Point2 | null>(null);
  const [visible, setVisible] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const lastPointer = useRef<Point2 | null>(null);
  /** True between the press and the release of a `keyboardDrag` tool. */
  const held = useRef(false);

  useEffect(() => {
    held.current = false;
  }, [tool]);

  const enabled = tool !== undefined && step !== undefined && step > 0;

  function pointer(world: Point2, shiftKey = false): CanvasPointer {
    return { world, scale: viewport.scale, entityId: null, shiftKey };
  }

  function onKeyDown(event: KeyboardEvent<Element>): boolean {
    if (!enabled || event.metaKey) return false;
    const start = position ?? lastPointer.current ?? [viewport.centerX, viewport.centerY];
    const delta = cursorDelta(event, step);
    if (delta !== null) {
      const next = movedCursor(start, delta);
      setPosition(next);
      setVisible(true);
      setAnnouncement(CURSOR_TEXT.at(next));
      tool.onPointerMove?.(pointer(next, event.shiftKey));
      const followed = followCursor(viewport, next);
      if (followed !== viewport) onPan(followed);
      consume(event);
      return true;
    }
    if (!PLACE_KEYS.has(event.key) || event.altKey || event.ctrlKey) return false;
    if (tool.ownsKey?.(event.key) === true) return false;
    const at = pointer(start, event.shiftKey);
    setPosition(start);
    setVisible(true);
    if (tool.keyboardDrag === true && held.current) {
      held.current = false;
      tool.onPointerUp?.(at);
    } else if (tool.onPointerDown?.(at) === true) {
      if (tool.keyboardDrag === true) held.current = true;
      else tool.onPointerUp?.(at);
    }
    setAnnouncement(CURSOR_TEXT.placed(start));
    consume(event);
    return true;
  }

  function notePointer(world: Point2): void {
    lastPointer.current = world;
    if (visible) {
      setVisible(false);
      setPosition(null);
    }
  }

  const arm = CROSSHAIR_PX / viewport.scale;
  const crosshair =
    enabled && visible && position !== null ? (
      <g
        data-testid="keyboard-cursor"
        pointerEvents="none"
        stroke={CROSSHAIR_COLOR}
        strokeWidth={2}
        fill="none"
      >
        <path
          d={
            `M${String(position[0] - arm)} ${String(-position[1])}h${String(2 * arm)}` +
            `M${String(position[0])} ${String(-position[1] - arm)}v${String(2 * arm)}`
          }
          vectorEffect="non-scaling-stroke"
        />
        <circle
          cx={position[0]}
          cy={-position[1]}
          r={arm / 2}
          vectorEffect="non-scaling-stroke"
        />
      </g>
    ) : null;

  return {
    onKeyDown,
    notePointer,
    onBlur: () => setVisible(false),
    crosshair,
    announcement: enabled ? announcement : "",
  };
}
